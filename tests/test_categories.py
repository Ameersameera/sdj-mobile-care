"""Product categories: add, rename, group/IMEI rules, hide/show, audit, permissions."""
import pytest

from sdj.core import audit, auth, inventory
from sdj.core.errors import PermissionDenied, ValidationError


def _new(name, cid, price=200):
    return inventory.create_product(dict(name=name, category_id=cid, purchase_price=price // 2, selling_price=price))


def test_added_category_is_listed_inside_its_group(db):
    cid = inventory.add_category("Car Chargers", "ACCESSORY")
    names = [c["name"] for c in inventory.list_categories()]
    assert names.index("Car Chargers") == names.index("Other Accessories") + 1
    assert names.index("Car Chargers") < names.index("Displays")
    c = inventory.get_category(cid)
    assert (c["group"], c["requires_imei"], c["is_active"], c["products"]) == ("ACCESSORY", False, True, 0)


def test_bad_or_duplicate_names_are_rejected(db):
    with pytest.raises(ValidationError):
        inventory.add_category("  chargers ", "ACCESSORY")          # built-in name, any case / spacing
    with pytest.raises(ValidationError):
        inventory.add_category("   ", "ACCESSORY")
    with pytest.raises(ValidationError):
        inventory.add_category("Toys", "TOYS")
    with pytest.raises(ValidationError):
        inventory.add_category("x" * 61, "SPARE")
    cid = inventory.add_category("Back  Panels", "SPARE")
    assert inventory.get_category(cid)["name"] == "Back Panels"     # extra spaces tidied
    inventory.set_category_active(cid, False)
    with pytest.raises(ValidationError, match="hidden"):
        inventory.add_category("back panels", "SPARE")


def test_edit_rename_imei_and_group_rules(db):
    cid = inventory.add_category("Smart Remotes", "SPARE")
    inventory.update_category(cid, "Smart TV Remotes", "ACCESSORY", True)      # unused: group may change
    c = inventory.get_category(cid)
    assert (c["name"], c["group"], c["requires_imei"]) == ("Smart TV Remotes", "ACCESSORY", True)
    pid = _new("Mi Remote", cid)
    assert inventory.get_product(pid)["track_imei"] is True                    # IMEI default comes from category
    with pytest.raises(ValidationError, match="group"):
        inventory.update_category(cid, "Smart TV Remotes", "SPARE", True)     # in use: group is locked
    inventory.update_category(cid, "TV Remotes", "ACCESSORY", False)          # rename is always fine
    c = inventory.get_category(cid)
    assert (c["name"], c["products"]) == ("TV Remotes", 1)
    assert inventory.get_product(pid)["track_imei"] is True                    # existing product unchanged
    with pytest.raises(ValidationError):
        inventory.update_category(cid, "Chargers", "ACCESSORY", False)


def test_hidden_category_keeps_old_products_but_not_new_ones(db):
    cid = inventory.add_category("Gaming Triggers", "ACCESSORY")
    pid = _new("PUBG Trigger", cid)
    inventory.set_category_active(cid, False)
    assert all(c["id"] != cid for c in inventory.list_categories())
    assert any(c["id"] == cid and not c["is_active"] for c in inventory.list_categories(active_only=False))
    inventory.update_product(pid, dict(name="PUBG Trigger Pro", category_id=cid, purchase_price=50, selling_price=150))
    assert inventory.get_product(pid)["name"] == "PUBG Trigger Pro"
    with pytest.raises(ValidationError, match="hidden"):
        _new("Another trigger", cid)
    inventory.set_category_active(cid, True)
    assert _new("Another trigger", cid)


def test_last_visible_category_cannot_be_hidden(db):
    cats = inventory.list_categories()
    for c in cats[1:]:
        inventory.set_category_active(c["id"], False)
    with pytest.raises(ValidationError):
        inventory.set_category_active(cats[0]["id"], False)


def test_category_changes_are_audited(db):
    cid = inventory.add_category("Car Mounts", "ACCESSORY")
    inventory.update_category(cid, "Car Holders", "ACCESSORY", False)
    inventory.set_category_active(cid, False)
    inventory.set_category_active(cid, True)
    actions = {a["action"] for a in audit.list_audit(50)}
    assert {"CATEGORY_CREATED", "CATEGORY_UPDATED", "CATEGORY_HIDDEN", "CATEGORY_SHOWN"} <= actions


def test_sales_staff_cannot_change_categories(db):
    auth.create_user("staff", "staff123", "CASHIER", "Counter staff")
    auth.set_current_user(auth.authenticate("staff", "staff123"))
    with pytest.raises(PermissionDenied):
        inventory.add_category("Car Chargers", "ACCESSORY")
    with pytest.raises(PermissionDenied):
        inventory.set_category_active(inventory.list_categories()[0]["id"], False)
