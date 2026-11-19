"""Owner-only data reset – clear practice data or wrong entries, module by module, by date range, or everything.

Safety rules
  * only the owner (admin), with their password and the word RESET;
  * a full backup is written first – *Undo last reset* puts it back;
  * the preview runs the very same code inside a transaction that is rolled back, so what it shows is exactly
    what the reset will remove;
  * financial tables are normally protected by delete triggers; a reset lifts them only inside its own
    transaction (SQLite DDL is transactional – on any error they come back untouched);
  * stock quantities and average costs are rebuilt from the remaining stock history, IMEI units and partly
    returned bills are put back in order, and closed days touched by the reset get their figures refreshed;
  * the audit log is never cleared – the reset itself is recorded there.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text

from ..db.base import D, q2, q4, session_scope
from ..db.init_db import PROTECTED_TABLES, TRIGGERS
from ..db.models import DailyClosing, ImeiRecord, Payment, Product, ReturnItem, Sale, SaleItem, SaleReturn, StockMovement
from . import settings
from .audit import audit
from .auth import check_own_password, current_user, require
from .business_day import current_business_date
from .calc import weighted_average
from .errors import BusinessError, PermissionDenied, ValidationError

log = logging.getLogger(__name__)

MODULES = ["sales", "repairs", "purchases", "expenses", "closings", "stock", "products", "customers", "suppliers"]
LABELS = {
    "sales": "Sales & bills", "repairs": "Repair jobs", "purchases": "Purchases", "expenses": "Expenses",
    "closings": "Daily closings", "stock": "Stock (set to 0)", "products": "Products", "customers": "Customers",
    "suppliers": "Suppliers", "all": "Everything (full reset)",
}
DATED = {"sales", "repairs", "purchases", "expenses", "closings"}
_DROP = [f"trg_nodelete_{t}" for t in PROTECTED_TABLES if t != "audit_logs"] + ["trg_noupdate_stock_movements",
                                                                                  "trg_locked_closing"]


class ResetBlocked(BusinessError):
    """The reset would leave the books inconsistent (e.g. stock below zero)."""


# ------------------------------------------------------------------ selection
def resolve(modules, start: date | None = None, end: date | None = None) -> tuple[set[str], date | None, date | None, bool]:
    mods = set(modules or ())
    unknown = mods - set(MODULES) - {"all"}
    if unknown:
        raise ValidationError(f"Unknown reset option: {', '.join(sorted(unknown))}")
    full = "all" in mods
    if full:
        mods = set(MODULES)
    if "products" in mods:                       # products can only go together with all of their history
        mods |= {"sales", "repairs", "purchases", "stock"}
    if not mods:
        raise ValidationError("Choose what to reset.")
    if full or "products" in mods or not (mods & DATED):
        start = end = None
    if (start is None) != (end is None):
        raise ValidationError("Choose both the From and To dates, or All time.")
    if start and start > end:
        raise ValidationError("The From date is after the To date.")
    return mods, start, end, full


def period_label(start: date | None, end: date | None) -> str:
    if start is None:
        return "All time"
    if start == end:
        return start.strftime("%d %b %Y")
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"


# ------------------------------------------------------------------ helpers
def _x(s, sql: str, **params):
    return s.execute(text(sql), params)


def _one(s, sql: str, **params):
    return _x(s, sql, **params).scalar() or 0


def _ids(s, name: str) -> None:
    _x(s, f"CREATE TEMP TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY)")
    _x(s, f"DELETE FROM {name}")


def _where_dates(col: str, start: date | None, end: date | None) -> tuple[str, dict]:
    if start is None:
        return "1=1", {}
    return f"{col} BETWEEN :a AND :b", {"a": start.isoformat(), "b": end.isoformat()}


def _money(v) -> Decimal:
    return q2(D(v or 0) / 100) if isinstance(v, int) else q2(v or 0)


def _sum_money(s, sql: str, **params) -> Decimal:
    """Money is stored as integer paise."""
    return q2(Decimal(_one(s, sql, **params)) / 100)


def _dates(s, sql: str, **params) -> set[date]:
    out = set()
    for (v,) in _x(s, sql, **params):
        if v:
            out.add(v if isinstance(v, date) else date.fromisoformat(str(v)[:10]))
    return out


# ------------------------------------------------------------------ the work (shared by preview and reset)
def _execute(s, mods: set[str], start: date | None, end: date | None, full: bool) -> dict:
    st: dict = {"counts": {}, "amounts": {}, "dates": set(), "products_touched": set(), "notes": []}
    c, a = st["counts"], st["amounts"]
    _x(s, "PRAGMA defer_foreign_keys = ON")
    for t in _DROP:
        _x(s, f"DROP TRIGGER IF EXISTS {t}")
    for name in ("rs_sales", "rs_returns", "rs_kept", "rs_imei", "rs_jobs", "rs_purchases", "rs_pitems", "rs_pimei",
                 "rs_products", "rs_customers", "rs_suppliers"):
        _ids(s, name)

    # ---------------- sales, quick sales, returns, exchanges
    if "sales" in mods:
        w, p = _where_dates("business_date", start, end)
        _x(s, f"INSERT INTO rs_sales SELECT id FROM sales WHERE {w}", **p)
        _x(s, f"INSERT OR IGNORE INTO rs_returns SELECT id FROM returns WHERE {w} "
              "OR sale_id IN (SELECT id FROM rs_sales)", **p)
        while True:      # an exchange bill belongs to its return, and the return to its bills
            n1 = _x(s, "INSERT OR IGNORE INTO rs_sales SELECT exchange_sale_id FROM returns "
                       "WHERE id IN (SELECT id FROM rs_returns) AND exchange_sale_id IS NOT NULL").rowcount
            n2 = _x(s, "INSERT OR IGNORE INTO rs_returns SELECT id FROM returns WHERE sale_id IN (SELECT id FROM rs_sales) "
                       "OR exchange_sale_id IN (SELECT id FROM rs_sales)").rowcount
            if n1 <= 0 and n2 <= 0:
                break
        resold = _x(s, "SELECT ir.imei1, sa2.invoice_no FROM return_items ri "
                       "JOIN sale_items si ON si.id = ri.sale_item_id "
                       "JOIN sale_items si2 ON si2.imei_record_id = si.imei_record_id AND si2.id <> si.id "
                       "JOIN sales sa2 ON sa2.id = si2.sale_id JOIN imei_records ir ON ir.id = si.imei_record_id "
                       "WHERE ri.return_id IN (SELECT id FROM rs_returns) AND si.sale_id NOT IN (SELECT id FROM rs_sales) "
                       "AND si2.sale_id NOT IN (SELECT id FROM rs_sales) AND sa2.status='COMPLETED' LIMIT 6").all()
        if resold:
            raise ResetBlocked("These returned phones were sold again later, so their returns can't be removed alone. "
                               "Choose dates that include the later bills too:\n"
                               + "\n".join(f"IMEI {i} – bill {n}" for i, n in resold))
        c["sales"] = _one(s, "SELECT COUNT(*) FROM sales WHERE id IN (SELECT id FROM rs_sales)")
        a["sales"] = _sum_money(s, "SELECT SUM(grand_total) FROM sales WHERE id IN (SELECT id FROM rs_sales) "
                                   "AND status='COMPLETED'")
        c["returns"] = _one(s, "SELECT COUNT(*) FROM rs_returns")
        pay_cond = ("(ref_type='SALE' AND ref_id IN (SELECT id FROM rs_sales)) OR (ref_type='SALE' AND direction='OUT' "
                    "AND kind IN ('REFUND','EXCHANGE_CREDIT') AND reference IN "
                    "(SELECT return_no FROM returns WHERE id IN (SELECT id FROM rs_returns)))")
        mv_cond = ("(ref_type='SALE' AND ref_id IN (SELECT id FROM rs_sales)) OR "
                   "(ref_type='RETURN' AND ref_id IN (SELECT id FROM rs_returns))")
        st["dates"] |= _dates(s, "SELECT business_date FROM sales WHERE id IN (SELECT id FROM rs_sales) UNION "
                                 "SELECT business_date FROM returns WHERE id IN (SELECT id FROM rs_returns) UNION "
                                 f"SELECT business_date FROM payments WHERE {pay_cond} UNION "
                                 f"SELECT business_date FROM stock_movements WHERE {mv_cond}")
        _x(s, f"INSERT OR IGNORE INTO rs_products SELECT DISTINCT product_id FROM stock_movements WHERE {mv_cond}")
        _x(s, "INSERT OR IGNORE INTO rs_imei SELECT imei_record_id FROM sale_items WHERE imei_record_id IS NOT NULL "
              "AND sale_id IN (SELECT id FROM rs_sales)")
        _x(s, "INSERT OR IGNORE INTO rs_imei SELECT id FROM imei_records WHERE sale_id IN (SELECT id FROM rs_sales)")
        _x(s, "INSERT OR IGNORE INTO rs_kept SELECT DISTINCT sale_id FROM returns WHERE id IN (SELECT id FROM rs_returns) "
              "AND sale_id NOT IN (SELECT id FROM rs_sales)")
        _x(s, "INSERT OR IGNORE INTO rs_imei SELECT si.imei_record_id FROM return_items ri JOIN sale_items si "
              "ON si.id = ri.sale_item_id WHERE ri.return_id IN (SELECT id FROM rs_returns) AND si.imei_record_id IS NOT NULL")
        _x(s, "DELETE FROM warranty_claims WHERE sale_id IN (SELECT id FROM rs_sales)")
        _x(s, f"DELETE FROM payments WHERE {pay_cond}")
        _x(s, f"DELETE FROM stock_movements WHERE {mv_cond}")
        _x(s, "UPDATE imei_records SET sale_id=NULL, sale_item_id=NULL WHERE sale_id IN (SELECT id FROM rs_sales) "
              "OR sale_item_id IN (SELECT id FROM sale_items WHERE sale_id IN (SELECT id FROM rs_sales))")
        _x(s, "DELETE FROM return_items WHERE return_id IN (SELECT id FROM rs_returns)")
        _x(s, "DELETE FROM returns WHERE id IN (SELECT id FROM rs_returns)")
        _x(s, "DELETE FROM sale_items WHERE sale_id IN (SELECT id FROM rs_sales)")
        _x(s, "DELETE FROM sales WHERE id IN (SELECT id FROM rs_sales)")
        _fix_kept_sales(s)
        _fix_imei(s)

    # ---------------- repair jobs (parts used go back to stock)
    if "repairs" in mods:
        w, p = _where_dates("received_date", start, end)
        _x(s, f"INSERT INTO rs_jobs SELECT id FROM service_jobs WHERE {w}", **p)
        c["repairs"] = _one(s, "SELECT COUNT(*) FROM rs_jobs")
        a["repairs"] = _sum_money(s, "SELECT SUM(total_amount) FROM service_jobs WHERE id IN (SELECT id FROM rs_jobs) "
                                     "AND status <> 'CANCELLED'")
        sel = "ref_type='SERVICE' AND ref_id IN (SELECT id FROM rs_jobs)"
        st["dates"] |= _dates(s, "SELECT received_date FROM service_jobs WHERE id IN (SELECT id FROM rs_jobs) UNION "
                                 "SELECT completed_date FROM service_jobs WHERE id IN (SELECT id FROM rs_jobs) UNION "
                                 "SELECT delivered_date FROM service_jobs WHERE id IN (SELECT id FROM rs_jobs) UNION "
                                 f"SELECT business_date FROM payments WHERE {sel} UNION "
                                 f"SELECT business_date FROM stock_movements WHERE {sel}")
        _x(s, f"INSERT OR IGNORE INTO rs_products SELECT DISTINCT product_id FROM stock_movements WHERE {sel}")
        _x(s, f"DELETE FROM payments WHERE {sel}")
        _x(s, f"DELETE FROM stock_movements WHERE {sel}")
        _x(s, "DELETE FROM service_items WHERE job_id IN (SELECT id FROM rs_jobs)")
        _x(s, "DELETE FROM service_jobs WHERE id IN (SELECT id FROM rs_jobs)")

    # ---------------- purchases (their stock leaves again)
    if "purchases" in mods:
        w, p = _where_dates("business_date", start, end)
        _x(s, f"INSERT INTO rs_purchases SELECT id FROM purchases WHERE {w}", **p)
        _x(s, "INSERT INTO rs_pitems SELECT id FROM purchase_items WHERE purchase_id IN (SELECT id FROM rs_purchases)")
        _x(s, "INSERT INTO rs_pimei SELECT id FROM imei_records WHERE purchase_item_id IN (SELECT id FROM rs_pitems)")
        sold = _x(s, "SELECT ir.imei1, sa.invoice_no FROM sale_items si JOIN sales sa ON sa.id = si.sale_id "
                     "JOIN imei_records ir ON ir.id = si.imei_record_id WHERE sa.status='COMPLETED' "
                     "AND si.imei_record_id IN (SELECT id FROM rs_pimei) LIMIT 6").all()
        if sold:
            raise ResetBlocked("These phones from the chosen purchases are already sold, so the purchases can't be "
                               "removed alone. Tick 'Sales & bills' for the same dates too:\n"
                               + "\n".join(f"IMEI {i} – bill {n}" for i, n in sold))
        c["purchases"] = _one(s, "SELECT COUNT(*) FROM rs_purchases")
        a["purchases"] = _sum_money(s, "SELECT SUM(total) FROM purchases WHERE id IN (SELECT id FROM rs_purchases) "
                                       "AND status <> 'CANCELLED'")
        sel = "ref_type='PURCHASE' AND ref_id IN (SELECT id FROM rs_purchases)"
        st["dates"] |= _dates(s, "SELECT business_date FROM purchases WHERE id IN (SELECT id FROM rs_purchases) UNION "
                                 f"SELECT business_date FROM payments WHERE {sel} UNION "
                                 f"SELECT business_date FROM stock_movements WHERE {sel}")
        _x(s, f"INSERT OR IGNORE INTO rs_products SELECT DISTINCT product_id FROM stock_movements WHERE {sel}")
        _x(s, f"DELETE FROM payments WHERE {sel}")
        _x(s, f"DELETE FROM stock_movements WHERE {sel}")
        _x(s, "DELETE FROM warranty_claims WHERE imei_record_id IN (SELECT id FROM rs_pimei)")
        _x(s, "UPDATE sale_items SET imei_record_id=NULL WHERE imei_record_id IN (SELECT id FROM rs_pimei)")
        _x(s, "DELETE FROM imei_records WHERE id IN (SELECT id FROM rs_pimei)")
        _x(s, "DELETE FROM purchase_items WHERE id IN (SELECT id FROM rs_pitems)")
        _x(s, "DELETE FROM purchases WHERE id IN (SELECT id FROM rs_purchases)")

    # ---------------- expenses
    if "expenses" in mods:
        w, p = _where_dates("business_date", start, end)
        c["expenses"] = _one(s, f"SELECT COUNT(*) FROM expenses WHERE {w}", **p)
        a["expenses"] = _sum_money(s, f"SELECT SUM(amount) FROM expenses WHERE {w} AND status='ACTIVE'", **p)
        st["dates"] |= _dates(s, f"SELECT business_date FROM expenses WHERE {w}", **p)
        _x(s, f"DELETE FROM expenses WHERE {w}", **p)

    # ---------------- stock: rebuild quantities & average cost from the history that is left
    if not ("products" in mods):
        negatives = _rebuild_stock(s)
        if negatives:
            raise ResetBlocked("Stock would go below zero for these products, because they were sold or used after "
                               "the entries you chose. Tick 'Sales & bills' / 'Repair jobs' for those dates too:\n"
                               + "\n".join(negatives[:8]))
    st["products_touched"] = {r[0] for r in _x(s, "SELECT id FROM rs_products")}

    # ---------------- products (with every unit, stock record and warranty claim)
    if "products" in mods:
        c["products"] = _one(s, "SELECT COUNT(*) FROM products")
        c["stock_units"] = _one(s, "SELECT SUM(quantity) FROM products WHERE quantity > 0")
        st["dates"] |= _dates(s, "SELECT DISTINCT business_date FROM stock_movements")
        _x(s, "DELETE FROM warranty_claims")
        _x(s, "DELETE FROM stock_movements")
        _x(s, "DELETE FROM imei_records")
        _x(s, "DELETE FROM products")
    elif "stock" in mods:
        c["stock_units"], c["stock_products"] = _zero_stock(s)
        if c["stock_products"]:
            st["dates"].add(current_business_date(s))

    # ---------------- daily closings
    if "closings" in mods:
        w, p = _where_dates("business_date", start, end)
        c["closings"] = _one(s, f"SELECT COUNT(*) FROM daily_closings WHERE {w}", **p)
        _x(s, f"DELETE FROM daily_closings WHERE {w}", **p)

    # ---------------- customers / suppliers that have nothing left
    if "customers" in mods:
        _x(s, "INSERT INTO rs_customers SELECT id FROM customers c WHERE is_walk_in = 0 "
              "AND NOT EXISTS (SELECT 1 FROM sales WHERE customer_id = c.id) "
              "AND NOT EXISTS (SELECT 1 FROM service_jobs WHERE customer_id = c.id) "
              "AND NOT EXISTS (SELECT 1 FROM returns WHERE customer_id = c.id) "
              "AND NOT EXISTS (SELECT 1 FROM warranty_claims WHERE customer_id = c.id) "
              "AND NOT EXISTS (SELECT 1 FROM imei_records WHERE customer_id = c.id) "
              "AND NOT EXISTS (SELECT 1 FROM payments WHERE customer_id = c.id AND ref_type <> 'OPENING')")
        c["customers"] = _one(s, "SELECT COUNT(*) FROM rs_customers")
        kept = _one(s, "SELECT COUNT(*) FROM customers WHERE is_walk_in = 0") - c["customers"]
        if kept:
            st["notes"].append(f"{kept} customer(s) stay because they still have bills or repair jobs.")
        st["dates"] |= _dates(s, "SELECT business_date FROM payments WHERE customer_id IN (SELECT id FROM rs_customers)")
        _x(s, "DELETE FROM payments WHERE customer_id IN (SELECT id FROM rs_customers)")
        _x(s, "DELETE FROM customers WHERE id IN (SELECT id FROM rs_customers)")
    if "suppliers" in mods:
        _x(s, "INSERT INTO rs_suppliers SELECT id FROM suppliers x WHERE "
              "NOT EXISTS (SELECT 1 FROM purchases WHERE supplier_id = x.id) "
              "AND NOT EXISTS (SELECT 1 FROM payments WHERE supplier_id = x.id AND ref_type <> 'OPENING')")
        c["suppliers"] = _one(s, "SELECT COUNT(*) FROM rs_suppliers")
        kept = _one(s, "SELECT COUNT(*) FROM suppliers") - c["suppliers"]
        if kept:
            st["notes"].append(f"{kept} supplier(s) stay because they still have purchases.")
        st["dates"] |= _dates(s, "SELECT business_date FROM payments WHERE supplier_id IN (SELECT id FROM rs_suppliers)")
        _x(s, "DELETE FROM payments WHERE supplier_id IN (SELECT id FROM rs_suppliers)")
        _x(s, "UPDATE products SET supplier_id=NULL WHERE supplier_id IN (SELECT id FROM rs_suppliers)")
        _x(s, "DELETE FROM suppliers WHERE id IN (SELECT id FROM rs_suppliers)")

    # ---------------- numbering starts again from 1 when nothing of that kind is left
    for table, pattern in (("sales", "invoice_%"), ("returns", "credit_note_%"), ("service_jobs", "job_no"),
                           ("purchases", "purchase_no"), ("warranty_claims", "claim_no"), ("products", "product_code")):
        if not _one(s, f"SELECT COUNT(*) FROM {table}"):
            _x(s, "DELETE FROM counters WHERE name LIKE :p", p=pattern)

    for ddl in TRIGGERS:
        _x(s, ddl)
    s.flush()
    bad = _x(s, "PRAGMA foreign_key_check").all()
    if bad:                                  # never leave broken links behind
        log.error("reset left foreign key problems: %s", bad[:10])
        raise BusinessError("The reset was stopped because it would leave broken links between records. "
                            "Nothing was changed.")
    st["dates"] = {d for d in st["dates"] if d}
    return st


def _fix_kept_sales(s) -> None:
    """Bills that stay but lost one of their returns: put their returned / refunded totals back in order."""
    kept = [r[0] for r in _x(s, "SELECT id FROM rs_kept")]
    for sid in kept:
        sale = s.get(Sale, sid)
        if sale is None:
            continue
        for si in sale.items:
            row = s.execute(select(func.coalesce(func.sum(ReturnItem.quantity), 0),
                                   func.coalesce(func.sum(ReturnItem.total_value), 0),
                                   func.coalesce(func.sum(ReturnItem.taxable_value), 0))
                            .where(ReturnItem.sale_item_id == si.id)).one()
            si.returned_qty = int(row[0] or 0)
            si.returned_value = _money(row[1])
            si.returned_taxable = _money(row[2])
        sale.returned_amount = _money(s.scalar(select(func.coalesce(func.sum(SaleReturn.total_value), 0))
                                               .where(SaleReturn.sale_id == sale.id)))
        sale.refunded_amount = _money(s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.ref_type == "SALE", Payment.ref_id == sale.id, Payment.direction == "OUT",
            Payment.status == "ACTIVE", Payment.kind.in_(("REFUND", "EXCHANGE_CREDIT")))))


def _fix_imei(s) -> None:
    """Phones whose bill or return was removed: sold to a remaining bill, damaged, removed, or back in stock."""
    for rid in [r[0] for r in _x(s, "SELECT id FROM rs_imei")]:
        rec = s.get(ImeiRecord, rid)
        if rec is None:
            continue
        si = s.scalar(select(SaleItem).join(Sale, Sale.id == SaleItem.sale_id)
                      .where(SaleItem.imei_record_id == rec.id, Sale.status == "COMPLETED",
                             SaleItem.returned_qty < SaleItem.quantity).order_by(SaleItem.id.desc()))
        if si is not None:
            sale = s.get(Sale, si.sale_id)
            rec.status, rec.sale_id, rec.sale_item_id = "SOLD", sale.id, si.id
            rec.customer_id, rec.sale_date, rec.warranty_expiry = sale.customer_id, sale.business_date, si.warranty_expiry
            continue
        rec.sale_id = rec.sale_item_id = rec.customer_id = None
        rec.sale_date = rec.warranty_expiry = None
        if rec.status == "REMOVED":
            continue
        damaged = s.scalar(select(ReturnItem.id).join(SaleItem, SaleItem.id == ReturnItem.sale_item_id)
                           .where(SaleItem.imei_record_id == rec.id, ReturnItem.condition == "DAMAGED"))
        rec.status = "DAMAGED" if damaged else "IN_STOCK"


def _rebuild_stock(s) -> list[str]:
    """Replay every remaining stock movement of the touched products (same maths as the stock gateway)."""
    negatives = []
    for (pid,) in _x(s, "SELECT id FROM rs_products").all():
        p = s.get(Product, pid)
        if p is None:
            continue
        qty, avg, low = 0, Decimal("0"), None
        for mv in s.scalars(select(StockMovement).where(StockMovement.product_id == pid).order_by(StockMovement.id)):
            ch = int(mv.qty_change)
            if ch > 0:
                avg = weighted_average(qty, avg, ch, D(mv.unit_cost or 0))
            elif mv.movement_type == "PURCHASE_CANCEL" and qty + ch > 0:
                rest = Decimal(qty) * avg - Decimal(-ch) * D(mv.unit_cost or 0)
                avg = q4(max(rest / Decimal(qty + ch), Decimal("0")))
            qty += ch
            if qty < 0 and low is None:
                low = qty
            mv.qty_after, mv.avg_cost_after = qty, q4(avg)
        if low is not None or qty < 0:
            negatives.append(f"{p.name} ({p.code})")
        p.quantity, p.avg_cost = qty, q4(avg)
    return negatives


def _zero_stock(s) -> tuple[int, int]:
    from .inventory import apply_movement
    bdate = current_business_date(s)
    units = products = 0
    for p in s.scalars(select(Product).where(Product.quantity != 0).order_by(Product.id)).all():
        q = int(p.quantity)
        apply_movement(s, p, -q, "ADJUST_OUT" if q > 0 else "ADJUST_IN", business_date=bdate, ref_type="ADJUSTMENT",
                       reference="RESET", notes="Stock reset to 0", allow_negative=True,
                       unit_cost=None if q > 0 else p.avg_cost)
        units += max(q, 0)
        products += 1
    for rec in s.scalars(select(ImeiRecord).where(ImeiRecord.status == "IN_STOCK")).all():
        rec.status = "REMOVED"
        rec.notes = (rec.notes + "\n" if rec.notes else "") + "Removed by stock reset"
    return units, products


# ------------------------------------------------------------------ public API
def _owner_only() -> None:
    require("settings")
    if current_user().role != "ADMIN":
        raise PermissionDenied("Only the owner (admin) can reset data.")


def preview_reset(modules, start: date | None = None, end: date | None = None) -> dict:
    """Exactly what a reset would remove – computed by doing it and rolling back."""
    _owner_only()
    mods, start, end, full = resolve(modules, start, end)
    from ..db import base
    session = base._db.factory()
    try:
        try:
            st = _execute(session, mods, start, end, full)
            st["blocked"] = ""
        except ResetBlocked as exc:
            st = {"counts": {}, "amounts": {}, "dates": set(), "notes": [], "blocked": str(exc)}
    finally:
        session.rollback()
        session.close()
    st.update(modules=sorted(mods), start=start, end=end, full=full, period=period_label(start, end))
    return st


def summary_lines(st: dict) -> list[str]:
    c, a = st.get("counts", {}), st.get("amounts", {})
    out = []
    if "sales" in c:
        out.append(f"{c['sales']} bill(s) worth ₹{a.get('sales', 0):,.2f}" + (f", {c['returns']} return(s)" if c.get("returns") else ""))
    if "repairs" in c:
        out.append(f"{c['repairs']} repair job(s) worth ₹{a.get('repairs', 0):,.2f}")
    if "purchases" in c:
        out.append(f"{c['purchases']} purchase(s) worth ₹{a.get('purchases', 0):,.2f}")
    if "expenses" in c:
        out.append(f"{c['expenses']} expense(s) worth ₹{a.get('expenses', 0):,.2f}")
    if "closings" in c:
        out.append(f"{c['closings']} daily closing(s)")
    if "products" in c:
        out.append(f"{c['products']} product(s) and {c.get('stock_units', 0)} unit(s) of stock")
    elif "stock_products" in c:
        out.append(f"Stock of {c['stock_products']} product(s) set to 0 ({c['stock_units']} unit(s))")
    if "customers" in c:
        out.append(f"{c['customers']} customer(s)")
    if "suppliers" in c:
        out.append(f"{c['suppliers']} supplier(s)")
    return out


def run_reset(modules, start: date | None = None, end: date | None = None, *, password: str, confirm: str) -> dict:
    _owner_only()
    if (confirm or "").strip().upper() != "RESET":
        raise ValidationError("Type RESET (in capital letters) to confirm.")
    check_own_password(password)
    mods, start, end, full = resolve(modules, start, end)
    from .backup import backup_now
    bk = backup_now("before_reset")          # no backup, no reset
    with session_scope() as s:
        st = _execute(s, mods, start, end, full)
        audit(s, "DATA_RESET", "database", "", {"modules": sorted(mods), "full": full, "period": period_label(start, end),
                                                "counts": st["counts"], "amounts": st["amounts"], "backup": str(bk)})
    refreshed = _refresh_closings(st["dates"])
    lines = summary_lines(st)
    settings.set_value("last_reset", {"at": datetime.now().replace(microsecond=0).isoformat(), "backup": str(bk),
                                      "period": period_label(start, end), "full": full,
                                      "modules": [LABELS[m] for m in MODULES if m in mods], "summary": lines})
    st.update(backup=str(bk), refreshed_closings=refreshed, summary=lines, period=period_label(start, end),
              full=full, modules=sorted(mods))
    return st


def _refresh_closings(dates: set[date]) -> int:
    """Closed days touched by a reset keep their counted cash, but their figures are recalculated."""
    from .reports import build_day_report
    done = 0
    for d in sorted(dates):
        try:
            with session_scope() as s:
                rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
                if rec is None:
                    continue
                opening, actual, carry, locked = rec.opening_cash, rec.actual_cash, rec.carry_forward, rec.is_locked
            rep = build_day_report(d, opening, actual, status="CLOSED" if locked else "REOPENED", carry_forward=carry)
            fin = {k.label: k.value for k in rep.section("Financial report").kvs}
            with session_scope() as s:
                rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
                rec.cash_received = rep.kv("Cash reconciliation", "Cash received")
                rec.cash_expenses = rep.kv("Cash reconciliation", "Cash expenses")
                rec.cash_refunds = rep.kv("Cash reconciliation", "Cash refunds")
                rec.cash_supplier_payments = rep.kv("Cash reconciliation", "Cash paid to suppliers")
                rec.expected_cash = rep.kv("Cash reconciliation", "Expected closing cash")
                rec.difference = rep.kv("Cash reconciliation", "Cash difference")
                rec.total_revenue = rep.kv("Summary", "Total revenue")
                rec.net_sales, rec.gross_profit = fin["Net sales (goods)"], fin["Gross profit"]
                rec.net_profit = fin["Net operating profit"]
                rec.summary_json = rep.to_json()
                rec.notes = ((rec.notes + "\n") if rec.notes else "") + f"Figures updated after a data reset on {date.today():%d %b %Y}."
                rec.is_locked = False            # step around the lock trigger for this one update …
                s.flush()
                rec.is_locked = locked           # … and lock it again
                pdf = rec.pdf_path
            done += 1
            if pdf:
                try:
                    from ..output.pdf_report import render_report_pdf
                    render_report_pdf(rep, pdf)
                except Exception:
                    log.exception("could not refresh closing PDF for %s", d)
        except Exception:
            log.exception("could not refresh the closing of %s", d)
    return done


def last_reset() -> dict | None:
    info = settings.get("last_reset") or None
    if not info or not info.get("backup") or not Path(info["backup"]).exists():
        return None
    return info


def undo_last_reset(password: str) -> Path:
    """Put back the backup taken just before the last reset (anything entered after the reset is lost)."""
    _owner_only()
    check_own_password(password)
    info = last_reset()
    if info is None:
        raise BusinessError("There is no reset to undo (or its backup file was removed).")
    from .backup import restore_backup
    safety = restore_backup(info["backup"])
    with session_scope() as s:
        audit(s, "DATA_RESET_UNDONE", "database", "", {"reset_at": info.get("at"), "backup": info["backup"],
                                                       "safety_copy": str(safety)})
    return safety
