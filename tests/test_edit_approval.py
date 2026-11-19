"""Edit bill (same invoice number) and owner / manager approval for staff."""
import json
from datetime import date
from decimal import Decimal as Dec

import pytest

from sdj.core import audit, auth, closing, inventory, returns, sales, settings
from sdj.core.errors import BusinessError, DayClosedError, PermissionDenied
from sdj.core.sales import CartItem
from tests.conftest import make_imei


@pytest.fixture
def goods(db, make_product):
    charger = make_product("Charger 20W", "Chargers", 250, 400, qty=50)
    cable = make_product("USB Cable", "USB Cables", 60, 150, qty=30)
    phone = make_product("Redmi 13C", "Smartphones", 9000, 11000, qty=2, imeis=[make_imei(1), make_imei(2)])
    return dict(charger=charger, cable=cable, phone=phone)


def _staff():
    auth.create_user("staff", "staff123", "CASHIER", "Counter staff")
    auth.set_current_user(auth.authenticate("staff", "staff123"))


def test_edit_bill_keeps_the_invoice_number(goods):
    c, cab, ph = goods["charger"], goods["cable"], goods["phone"]
    u1, u2 = inventory.available_imeis(ph)
    s1 = sales.create_sale([CartItem(c, 2, Dec("400")), CartItem(ph, 1, Dec("11000"), imei_record_id=u1["id"])],
                           payments={"CASH": Dec("11800")})
    no = s1["invoice_no"]
    res = sales.amend_sale(s1["id"], [CartItem(c, 1, Dec("380")), CartItem(cab, 2, Dec("150")),
                                      CartItem(ph, 1, Dec("11000"), imei_record_id=u2["id"])],
                           payments={"UPI": Dec("11680")})
    assert res["invoice_no"] == no and res["old_version"] == f"{no}-E1" and res["grand_total"] == Dec("11680.00")
    old = sales.get_sale(s1["id"])
    assert old["status"] == "CANCELLED" and old["invoice_no"] == f"{no}-E1"
    assert inventory.get_product(c)["quantity"] == 49 and inventory.get_product(cab)["quantity"] == 28
    left = {u["id"] for u in inventory.available_imeis(ph)}
    assert left == {u1["id"]}                          # the first phone is free again, the second is sold
    assert sales.get_sale(res["id"])["paid"] == Dec("11680.00")
    again = sales.amend_sale(res["id"], [CartItem(c, 1, Dec("400"))], payments={"CASH": Dec("400")})
    assert again["invoice_no"] == no and again["old_version"] == f"{no}-E2"
    assert any(a["action"] == "SALE_EDITED" for a in audit.list_audit(20))


def test_edit_is_refused_after_return_or_closing(goods):
    c = goods["charger"]
    s1 = sales.create_sale([CartItem(c, 2, Dec("400"))], payments={"CASH": Dec("800")})
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1)], reason="Faulty", refund_mode="CASH")
    with pytest.raises(BusinessError):
        sales.amend_sale(s1["id"], [CartItem(c, 1, Dec("400"))], payments={"CASH": Dec("400")})
    s2 = sales.create_sale([CartItem(c, 1, Dec("400"))], payments={"CASH": Dec("400")})
    closing.close_day(date.today(), actual_cash=Dec("0"), opening_cash=0, make_pdf=False, make_backup=False)
    with pytest.raises(DayClosedError):
        sales.amend_sale(s2["id"], [CartItem(c, 1, Dec("350"))], payments={"CASH": Dec("350")})


def test_staff_cancel_needs_approval_and_it_is_audited(goods):
    s1 = sales.create_sale([CartItem(goods["charger"], 1, Dec("400"))], payments={"CASH": Dec("400")})
    _staff()
    with pytest.raises(PermissionDenied) as exc:
        sales.cancel_sale(s1["id"], "Wrong item")
    assert exc.value.perm == "cancel_sale"
    with pytest.raises(BusinessError):
        auth.verify_approver("admin", "nope", "cancel_sale")
    with pytest.raises(BusinessError):
        auth.verify_approver("staff", "staff123", "cancel_sale")          # staff can't approve themselves
    owner = auth.verify_approver("admin", "admin123", "cancel_sale")
    with auth.approved(owner, {"cancel_sale"}):
        sales.cancel_sale(s1["id"], "Wrong item")
    assert not auth.current_user().can("cancel_sale")                    # approval was only for that action
    assert sales.get_sale(s1["id"])["status"] == "CANCELLED"
    entry = next(a for a in audit.list_audit(20) if a["action"] == "SALE_CANCELLED")
    assert json.loads(entry["details"])["approved_by"] == "admin" and entry["username"] == "staff"


def test_staff_discount_limit(goods):
    settings.set_value("staff_discount_limit", 10)
    c = goods["charger"]
    sales.create_sale([CartItem(c, 1, Dec("400"), Dec("100"))], payments={"CASH": Dec("300")})   # owner: no limit
    _staff()
    sales.create_sale([CartItem(c, 1, Dec("400"), Dec("40"))], payments={"CASH": Dec("360")})    # 10 % is fine
    with pytest.raises(PermissionDenied) as exc:
        sales.create_sale([CartItem(c, 1, Dec("400"))], bill_discount=Dec("60"), payments={"CASH": Dec("340")})
    assert exc.value.perm == "discount_override" and "15.0%" in str(exc.value)
    owner = auth.verify_approver("admin", "admin123", "discount_override")
    with auth.approved(owner, {"discount_override"}):
        sales.create_sale([CartItem(c, 1, Dec("400"))], bill_discount=Dec("60"), payments={"CASH": Dec("340")})
    settings.set_value("staff_discount_limit", 0)
    sales.create_sale([CartItem(c, 1, Dec("400"), Dec("200"))], payments={"CASH": Dec("200")})   # 0 = no limit
