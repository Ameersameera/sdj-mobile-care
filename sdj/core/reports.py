"""Accounting engine and every report – all figures come from real transactions.

Formulas (all amounts excluding GST):
    Net counter sales  = Gross sales − Discounts − Sales returns (+ round-off)
    Net sales (goods)  = Net counter sales + Spare parts used in service
    COGS               = cost of goods sold (weighted average) − cost of restocked returns
                         + cost of spare parts used in completed jobs
    Gross profit       = Net sales − COGS
    Service income     = Labour + Spare-parts revenue (each counted once)
    Net operating profit = Gross profit + Service labour − Operating expenses
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from ..db.base import session_scope
from ..db.models import (Expense, Payment, Product, Purchase, ReturnItem, Sale, SaleItem, SaleReturn,
                         ServiceItem, ServiceJob, StockMovement)
from ..output.fmt import d_only
from ..output.report_model import KV, Col, Report, Section, Table
from . import settings
from .business_day import current_business_date
from .inventory import GROUP_LABELS
from .repairs import OPEN_STATUSES
from .sales import MODE_LABELS

ZERO = Decimal("0.00")
GROUPS = ("MOBILE", "ACCESSORY", "SPARE")
MODES = ("CASH", "UPI", "CARD", "OTHER")


def _P(v) -> Decimal:
    return (Decimal(int(v or 0)) / 100).quantize(Decimal("0.01"))


def _C(v) -> Decimal:
    return (Decimal(int(v or 0)) / 10000).quantize(Decimal("0.0001"))


def _dd():
    return defaultdict(lambda: ZERO)


# =========================================================================== core figures
def period_figures(start: date, end: date) -> dict:
    f: dict = defaultdict(lambda: ZERO)
    by_group = {g: dict(taxable=ZERO, qty=0, cost=ZERO, returns=ZERO, return_qty=0) for g in GROUPS}
    by_product: dict[int, dict] = defaultdict(lambda: dict(name="", qty=0, taxable=ZERO, cost=ZERO, ret_qty=0,
                                                           ret_taxable=ZERO, ret_cost=ZERO, svc_qty=0,
                                                           svc_taxable=ZERO, svc_cost=ZERO))
    daily: dict[date, dict] = defaultdict(_dd)
    pay_in, refunds_out, supplier_out = _dd(), _dd(), _dd()
    expenses_by_cat, expenses_by_mode = _dd(), _dd()
    tech: dict[str, dict] = defaultdict(lambda: dict(received=0, completed=0, delivered=0, labour=ZERO,
                                                     parts_rev=ZERO, parts_cost=ZERO))
    counts = defaultdict(int)
    with session_scope() as s:
        # ---------------- counter sales
        q = select(SaleItem.product_id, SaleItem.product_name, SaleItem.category_group, SaleItem.quantity,
                   SaleItem.gross_ex_tax, SaleItem.discount_ex_tax, SaleItem.taxable_value, SaleItem.gst_amount,
                   SaleItem.cost_total, Sale.business_date).join(Sale) \
            .where(Sale.status == "COMPLETED", Sale.business_date >= start, Sale.business_date <= end)
        for pid, pname, grp, qty, g_ex, d_ex, taxable, gst, cost, d in s.execute(q):
            f["gross_ex"] += g_ex
            f["discount_ex"] += d_ex
            f["counter_taxable"] += taxable
            f["sales_gst"] += gst
            f["counter_cost"] += cost
            counts["qty_sold"] += qty
            bg = by_group.setdefault(grp or "ACCESSORY", dict(taxable=ZERO, qty=0, cost=ZERO, returns=ZERO, return_qty=0))
            bg["taxable"] += taxable
            bg["qty"] += qty
            bg["cost"] += cost
            bp = by_product[pid]
            bp["name"], bp["qty"], bp["taxable"], bp["cost"] = pname, bp["qty"] + qty, bp["taxable"] + taxable, bp["cost"] + cost
            daily[d]["counter_taxable"] += taxable
            daily[d]["cogs"] += cost
            daily[d]["qty"] += qty
        q = select(Sale.business_date, Sale.round_off, Sale.initial_due, Sale.grand_total, Sale.sale_type) \
            .where(Sale.status == "COMPLETED", Sale.business_date >= start, Sale.business_date <= end)
        for d, rnd, due, total, stype in s.execute(q):
            counts["invoices"] += 1
            if stype == "QUICK":
                counts["quick_sales"] += 1
            f["round_off"] += rnd
            f["credit_sales"] += due
            f["bill_total"] += total
            daily[d]["round_off"] += rnd
            daily[d]["invoices"] += 1
        counts["cancelled_bills"] = len(s.execute(select(Sale.id).where(
            Sale.status == "CANCELLED", Sale.business_date >= start, Sale.business_date <= end)).all())
        # ---------------- returns
        q = select(ReturnItem.product_id, ReturnItem.product_name, ReturnItem.category_group, ReturnItem.quantity,
                   ReturnItem.taxable_value, ReturnItem.gst_value, ReturnItem.cost_total, ReturnItem.restocked,
                   SaleReturn.business_date).join(SaleReturn) \
            .where(SaleReturn.business_date >= start, SaleReturn.business_date <= end)
        for pid, pname, grp, qty, taxable, gst, cost, restocked, d in s.execute(q):
            f["returns_taxable"] += taxable
            f["returns_gst"] += gst
            counts["qty_returned"] += qty
            if restocked:
                f["cost_reversed"] += cost
            else:
                f["damaged_cost"] += cost
                counts["qty_damaged"] += qty
            bg = by_group.setdefault(grp or "ACCESSORY", dict(taxable=ZERO, qty=0, cost=ZERO, returns=ZERO, return_qty=0))
            bg["returns"] += taxable
            bg["return_qty"] += qty
            bp = by_product[pid]
            bp["name"] = bp["name"] or pname
            bp["ret_qty"] += qty
            bp["ret_taxable"] += taxable
            if restocked:
                bp["ret_cost"] += cost
                daily[d]["cogs"] -= cost
            daily[d]["returns_taxable"] += taxable
        for d, rnd, refund, value in s.execute(select(SaleReturn.business_date, SaleReturn.round_off_value,
                                                      SaleReturn.refund_amount, SaleReturn.total_value)
                                               .where(SaleReturn.business_date >= start, SaleReturn.business_date <= end)):
            counts["returns"] += 1
            f["round_off"] -= rnd
            f["returns_value_incl"] += value
            daily[d]["round_off"] -= rnd
        # ---------------- service (income recognised on completion)
        jobs = s.scalars(select(ServiceJob).where(ServiceJob.completed_date >= start, ServiceJob.completed_date <= end,
                                                  ServiceJob.status != "CANCELLED")).all()
        job_ids = []
        for j in jobs:
            job_ids.append(j.id)
            counts["jobs_completed"] += 1
            f["labour"] += j.labour_taxable
            f["parts_revenue"] += j.parts_taxable
            f["parts_cost"] += j.parts_cost
            f["service_gst"] += j.labour_gst + j.parts_gst
            f["service_total_incl"] += j.total_amount
            d = j.completed_date
            daily[d]["labour"] += j.labour_taxable
            daily[d]["parts_rev"] += j.parts_taxable
            daily[d]["cogs"] += j.parts_cost
            t = tech[j.technician or "Unassigned"]
            t["completed"] += 1
            t["labour"] += j.labour_taxable
            t["parts_rev"] += j.parts_taxable
            t["parts_cost"] += j.parts_cost
        if job_ids:
            for chunk in [job_ids[i:i + 500] for i in range(0, len(job_ids), 500)]:
                for pid, pname, qty, taxable, cost in s.execute(
                        select(ServiceItem.product_id, ServiceItem.product_name, ServiceItem.quantity,
                               ServiceItem.taxable_value, ServiceItem.cost_total)
                        .where(ServiceItem.job_id.in_(chunk), ServiceItem.status == "ACTIVE")):
                    bp = by_product[pid]
                    bp["name"] = bp["name"] or pname
                    bp["svc_qty"] += qty
                    bp["svc_taxable"] += taxable
                    bp["svc_cost"] += cost
                    counts["parts_in_completed_jobs"] += qty
        for tname, n in s.execute(select(ServiceJob.technician, ServiceJob.id).where(
                ServiceJob.received_date >= start, ServiceJob.received_date <= end)):
            counts["jobs_received"] += 1
            tech[tname or "Unassigned"]["received"] += 1
        for tname, st in s.execute(select(ServiceJob.technician, ServiceJob.status).where(
                ServiceJob.delivered_date >= start, ServiceJob.delivered_date <= end)):
            counts["jobs_delivered"] += 1
            tech[tname or "Unassigned"]["delivered"] += 1
        for st, in s.execute(select(ServiceJob.status).where(ServiceJob.received_date <= end)):
            if st in OPEN_STATUSES:
                counts["jobs_pending"] += 1
            elif st == "READY":
                counts["jobs_ready"] += 1
        counts["jobs_cancelled"] = len(s.execute(select(ServiceJob.id).where(
            ServiceJob.status == "CANCELLED", ServiceJob.received_date >= start, ServiceJob.received_date <= end)).all())
        used = s.execute(text("SELECT COALESCE(SUM(qty_change),0) FROM stock_movements WHERE movement_type IN "
                              "('SERVICE_USE','SERVICE_RETURN') AND business_date BETWEEN :a AND :b"),
                         {"a": start.isoformat(), "b": end.isoformat()}).scalar()
        counts["parts_consumed"] = -int(used or 0)
        # ---------------- expenses
        for d, cat, amt, mode in s.execute(select(Expense.business_date, Expense.category, Expense.amount,
                                                  Expense.payment_mode).where(Expense.status == "ACTIVE",
                                                                              Expense.business_date >= start,
                                                                              Expense.business_date <= end)):
            f["expenses"] += amt
            expenses_by_cat[cat] += amt
            expenses_by_mode[mode] += amt
            daily[d]["expenses"] += amt
            counts["expense_entries"] += 1
        # ---------------- purchases
        for total, gst in s.execute(select(Purchase.total, Purchase.gst_amount).where(
                Purchase.status == "ACTIVE", Purchase.business_date >= start, Purchase.business_date <= end)):
            counts["purchases"] += 1
            f["purchases"] += total
            f["purchase_gst"] += gst
        # ---------------- payments
        for d, direction, mode, amt, kind in s.execute(select(Payment.business_date, Payment.direction, Payment.mode,
                                                              Payment.amount, Payment.kind)
                                                       .where(Payment.status == "ACTIVE", Payment.business_date >= start,
                                                              Payment.business_date <= end)):
            if mode == "EXCHANGE":
                f["exchange_adjusted"] += amt if direction == "IN" else ZERO
                continue
            if direction == "IN":
                pay_in[mode] += amt
                if kind in ("DUE_COLLECTION", "OPENING_DUE"):
                    f["due_collections"] += amt
                if kind == "SERVICE_ADVANCE":
                    f["service_advances"] += amt
                daily[d]["collected"] += amt
            else:
                if kind == "SUPPLIER_PAYMENT":
                    supplier_out[mode] += amt
                else:
                    refunds_out[mode] += amt
                    daily[d]["refunds"] += amt
        # ---------------- stock adjustments
        for qty_change, unit_cost, mtype in s.execute(select(StockMovement.qty_change, StockMovement.unit_cost,
                                                             StockMovement.movement_type)
                                                      .where(StockMovement.movement_type.in_(("ADJUST_IN", "ADJUST_OUT", "DAMAGED")),
                                                             StockMovement.business_date >= start,
                                                             StockMovement.business_date <= end)):
            f["adjustment_value"] += (Decimal(qty_change) * unit_cost).quantize(Decimal("0.01"))
    # ---------------- derived figures
    f["net_counter_sales"] = f["gross_ex"] - f["discount_ex"] - f["returns_taxable"] + f["round_off"]
    f["net_sales"] = f["net_counter_sales"] + f["parts_revenue"]
    f["counter_cogs"] = f["counter_cost"] - f["cost_reversed"]
    f["cogs"] = f["counter_cogs"] + f["parts_cost"]
    f["gross_profit"] = f["net_sales"] - f["cogs"]
    f["service_income"] = f["labour"] + f["parts_revenue"]
    f["parts_profit"] = f["parts_revenue"] - f["parts_cost"]
    f["service_profit"] = f["labour"] + f["parts_profit"]
    f["net_operating_profit"] = f["gross_profit"] + f["labour"] - f["expenses"]
    f["total_revenue"] = f["net_counter_sales"] + f["service_income"]
    f["gst_output"] = f["sales_gst"] - f["returns_gst"] + f["service_gst"]
    f["gst_input"] = f["purchase_gst"] if settings.get("gst_enabled") else ZERO
    f["collections_total"] = sum(pay_in.values(), ZERO)
    f["refunds_total"] = sum(refunds_out.values(), ZERO)
    f["supplier_paid_total"] = sum(supplier_out.values(), ZERO)
    for g in GROUPS:
        by_group.setdefault(g, dict(taxable=ZERO, qty=0, cost=ZERO, returns=ZERO, return_qty=0))
        by_group[g]["net"] = by_group[g]["taxable"] - by_group[g]["returns"]
    for dd in daily.values():
        dd["net_counter"] = dd["counter_taxable"] - dd["returns_taxable"] + dd["round_off"]
        dd["net_sales"] = dd["net_counter"] + dd["parts_rev"]
        dd["revenue"] = dd["net_counter"] + dd["labour"] + dd["parts_rev"]
        dd["gross_profit"] = dd["net_sales"] - dd["cogs"]
        dd["net_profit"] = dd["gross_profit"] + dd["labour"] - dd["expenses"]
        dd["service_income"] = dd["labour"] + dd["parts_rev"]
    f["spare_parts_sold"] = Decimal(by_group["SPARE"]["qty"] + counts["parts_consumed"])
    return dict(start=start, end=end, f=f, counts=counts, by_group=by_group, by_product=dict(by_product),
                daily=dict(daily), pay_in={m: pay_in[m] for m in MODES}, refunds_out={m: refunds_out[m] for m in MODES},
                supplier_out={m: supplier_out[m] for m in MODES}, expenses_by_cat=dict(expenses_by_cat),
                expenses_by_mode=dict(expenses_by_mode), technicians={k: dict(v) for k, v in tech.items()})


# =========================================================================== product-wise
def product_wise(start: date, end: date, figures: dict | None = None) -> list[dict]:
    figures = figures or period_figures(start, end)
    bp = figures["by_product"]
    a, b = start.isoformat(), end.isoformat()
    with session_scope() as s:
        prods = s.execute(select(Product.id, Product.code, Product.name, Product.category_id, Product.selling_price,
                                 Product.avg_cost, Product.quantity, Product.is_active)).all()
        from ..db.models import Category
        cats = {c.id: c for c in s.scalars(select(Category))}
        opening = dict(s.execute(text("SELECT product_id, SUM(qty_change) FROM stock_movements WHERE business_date < :a "
                                      "GROUP BY product_id"), {"a": a}).all())
        mv = defaultdict(lambda: defaultdict(int))
        for pid, mtype, q in s.execute(text("SELECT product_id, movement_type, SUM(qty_change) FROM stock_movements "
                                            "WHERE business_date BETWEEN :a AND :b GROUP BY product_id, movement_type"),
                                       {"a": a, "b": b}):
            mv[pid][mtype] = int(q or 0)
        hist_avg = {}
        if end < date.today():
            for pid, avg in s.execute(text("SELECT product_id, avg_cost_after FROM stock_movements WHERE id IN "
                                           "(SELECT MAX(id) FROM stock_movements WHERE business_date <= :b GROUP BY product_id)"),
                                      {"b": b}):
                hist_avg[pid] = _C(avg)
    rows = []
    for pid, code, name, cat_id, sell, avg, cur_qty, active in prods:
        m = mv.get(pid, {})
        op = int(opening.get(pid) or 0)
        purchased = m.get("PURCHASE", 0) + m.get("PURCHASE_CANCEL", 0)
        sold = -(m.get("SALE", 0) + m.get("SALE_CANCEL", 0))
        service = -(m.get("SERVICE_USE", 0) + m.get("SERVICE_RETURN", 0))
        returned = m.get("SALE_RETURN", 0)
        adjusted = m.get("OPENING", 0) + m.get("ADJUST_IN", 0) + m.get("ADJUST_OUT", 0) + m.get("DAMAGED", 0)
        closing = op + purchased - sold - service + returned + adjusted
        x = bp.get(pid, {})
        sales_value = x.get("taxable", ZERO) - x.get("ret_taxable", ZERO) + x.get("svc_taxable", ZERO)
        cogs = x.get("cost", ZERO) - x.get("ret_cost", ZERO) + x.get("svc_cost", ZERO)
        unit_cost = hist_avg.get(pid, avg) if end < date.today() else avg
        if not (op or m or closing or sales_value or cogs):
            continue
        cat = cats.get(cat_id)
        rows.append(dict(product_id=pid, code=code, name=name, category=cat.name if cat else "",
                         group=cat.group if cat else "", opening=op, purchased=purchased, sold=sold, service=service,
                         returned=returned, adjusted=adjusted, closing=closing, avg_cost=unit_cost, selling_price=sell,
                         sales_value=sales_value, cogs=cogs, gross_profit=sales_value - cogs,
                         stock_value=(Decimal(closing) * unit_cost).quantize(Decimal("0.01")) if closing > 0 else ZERO,
                         active=active))
    order = {g: i for i, g in enumerate(GROUPS)}
    rows.sort(key=lambda r: (order.get(r["group"], 9), r["name"].lower()))
    return rows


# =========================================================================== dashboard & charts
def dashboard_data() -> dict:
    from .inventory import stock_summary
    from .parties import total_receivables
    with session_scope() as s:
        bdate = current_business_date(s)
    fig = period_figures(bdate, bdate)
    return dict(business_date=bdate, fig=fig, stock=stock_summary(), receivables=total_receivables(),
                recent=recent_transactions(12))


def recent_transactions(limit: int = 12) -> list[dict]:
    items = []
    with session_scope() as s:
        for x in s.scalars(select(Sale).order_by(Sale.id.desc()).limit(limit)):
            items.append(dict(time=x.created_at, type="Quick sale" if x.sale_type == "QUICK" else
                              ("Exchange" if x.sale_type == "EXCHANGE" else "Sale"), ref=x.invoice_no,
                              party=x.customer_name, amount=x.grand_total,
                              status="Cancelled" if x.status == "CANCELLED" else ("Credit" if x.initial_due > 0 else "Paid")))
        for j in s.scalars(select(ServiceJob).order_by(ServiceJob.updated_at.desc()).limit(limit)):
            items.append(dict(time=j.updated_at, type="Service", ref=j.job_no, party=j.customer_name,
                              amount=j.total_amount, status=j.status.replace("_", " ").title()))
        for e in s.scalars(select(Expense).where(Expense.status == "ACTIVE").order_by(Expense.id.desc()).limit(limit)):
            items.append(dict(time=e.created_at, type="Expense", ref=e.category, party=e.description,
                              amount=-e.amount, status=MODE_LABELS.get(e.payment_mode, e.payment_mode)))
        for p in s.scalars(select(Purchase).order_by(Purchase.id.desc()).limit(5)):
            items.append(dict(time=p.created_at, type="Purchase", ref=p.purchase_no, party=p.supplier.name,
                              amount=-p.total, status=p.status.title()))
        for r in s.scalars(select(SaleReturn).order_by(SaleReturn.id.desc()).limit(5)):
            items.append(dict(time=r.created_at, type="Return", ref=r.return_no, party=r.reason, amount=-r.total_value,
                              status=r.return_type.title()))
    items.sort(key=lambda x: x["time"] or datetime.min, reverse=True)
    return items[:limit]


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_month(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def chart_data() -> dict:
    """Series for all dashboard graphs, from one pass over the last 12 months."""
    with session_scope() as s:
        today = current_business_date(s)
    first_month = _add_month(_month_start(today), -11)
    fig = period_figures(first_month, today)
    daily = fig["daily"]

    def val(d, key):
        return daily.get(d, {}).get(key, ZERO)

    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    weeks = []
    week_start = today - timedelta(days=today.weekday())
    for i in range(7, -1, -1):
        ws = week_start - timedelta(weeks=i)
        weeks.append(ws)
    months = [_add_month(first_month, i) for i in range(12)]

    def month_sum(ms, key):
        me = _add_month(ms, 1)
        return sum((v.get(key, ZERO) for d, v in daily.items() if ms <= d < me), ZERO)

    def week_sum(ws, key):
        we = ws + timedelta(days=7)
        return sum((v.get(key, ZERO) for d, v in daily.items() if ws <= d < we), ZERO)

    month_start = _month_start(today)
    month_products = period_figures(month_start, today)["by_product"]
    top = sorted(((v["name"], v["taxable"] - v["ret_taxable"] + v["svc_taxable"]) for v in month_products.values()),
                 key=lambda t: t[1], reverse=True)[:10]
    return dict(
        daily=dict(labels=[d.strftime("%d %b") for d in days], revenue=[val(d, "revenue") for d in days]),
        weekly=dict(labels=[w.strftime("%d %b") for w in weeks], revenue=[week_sum(w, "revenue") for w in weeks]),
        monthly=dict(labels=[m.strftime("%b %y") for m in months], revenue=[month_sum(m, "revenue") for m in months]),
        rev_exp=dict(labels=[m.strftime("%b %y") for m in months[-6:]],
                     revenue=[month_sum(m, "revenue") for m in months[-6:]],
                     expenses=[month_sum(m, "expenses") for m in months[-6:]]),
        products=dict(labels=[t[0] for t in top], values=[t[1] for t in top]),
        service=dict(labels=[d.strftime("%d %b") for d in days], labour=[val(d, "labour") for d in days],
                     parts=[val(d, "parts_rev") for d in days]),
        profit=dict(labels=[m.strftime("%b %y") for m in months],
                    gross=[month_sum(m, "gross_profit") for m in months],
                    net=[month_sum(m, "net_profit") for m in months]),
    )


# =========================================================================== report builders
def _shop_period(start: date, end: date) -> str:
    return d_only(start) if start == end else f"{d_only(start)} to {d_only(end)}"


def financial_kvs(f: dict) -> list[KV]:
    return [
        KV("Gross sales (counter, excl. GST)", f["gross_ex"]),
        KV("Less: Discounts", f["discount_ex"]),
        KV("Less: Sales returns", f["returns_taxable"]),
        KV("Round-off adjustment", f["round_off"]),
        KV("Net counter sales", f["net_counter_sales"], strong=True),
        KV("Add: Spare parts used in service", f["parts_revenue"]),
        KV("Net sales (goods)", f["net_sales"], strong=True),
        KV("Less: Cost of goods sold", f["cogs"]),
        KV("Gross profit", f["gross_profit"], strong=True, tone="good" if f["gross_profit"] >= 0 else "bad"),
        KV("Add: Service labour income", f["labour"]),
        KV("Less: Operating expenses", f["expenses"]),
        KV("Net operating profit", f["net_operating_profit"], strong=True,
           tone="good" if f["net_operating_profit"] >= 0 else "bad"),
        KV("GST collected (output)", f["gst_output"]),
        KV("Total revenue (sales + service)", f["total_revenue"], strong=True),
    ]


def payment_kvs(fig: dict) -> list[KV]:
    f = fig["f"]
    kvs = [KV(f"{MODE_LABELS[m]} received", fig["pay_in"][m]) for m in MODES]
    kvs += [KV("Total collected", f["collections_total"], strong=True),
            KV("Credit sales (given today)", f["credit_sales"]),
            KV("Customer dues collected", f["due_collections"]),
            KV("Refunds paid", f["refunds_total"]),
            KV("Paid to suppliers", f["supplier_paid_total"])]
    if f.get("exchange_adjusted"):
        kvs.append(KV("Exchange credit adjusted (non-cash)", f["exchange_adjusted"]))
    return kvs


def category_kvs(fig: dict) -> list[KV]:
    bg, f = fig["by_group"], fig["f"]
    return [KV("Mobile phone sales", bg["MOBILE"]["net"]), KV("Accessories sales", bg["ACCESSORY"]["net"]),
            KV("Spare parts sales (counter)", bg["SPARE"]["net"]),
            KV("Spare parts used in service", f["parts_revenue"]),
            KV("Qty sold – mobiles", bg["MOBILE"]["qty"], "int"), KV("Qty sold – accessories", bg["ACCESSORY"]["qty"], "int"),
            KV("Qty sold – spare parts (counter)", bg["SPARE"]["qty"], "int"),
            KV("Spare parts consumed in service", fig["counts"].get("parts_consumed", 0), "int")]


def service_kvs(fig: dict) -> list[KV]:
    f, c = fig["f"], fig["counts"]
    return [KV("Jobs received", c.get("jobs_received", 0), "int"), KV("Jobs completed", c.get("jobs_completed", 0), "int"),
            KV("Jobs delivered", c.get("jobs_delivered", 0), "int"), KV("Jobs pending (open now)", c.get("jobs_pending", 0), "int"),
            KV("Ready, awaiting delivery", c.get("jobs_ready", 0), "int"),
            KV("Labour income", f["labour"]), KV("Spare parts revenue", f["parts_revenue"]),
            KV("Spare parts cost", f["parts_cost"]), KV("Service income (labour + parts)", f["service_income"], strong=True),
            KV("Service profit", f["service_profit"], strong=True, tone="good" if f["service_profit"] >= 0 else "bad")]


def product_table(rows: list[dict], with_value: bool = True) -> Table:
    cols = [Col("Product", width=2.7), Col("Category", width=1.4), Col("Open", "int", 0.72), Col("Purch.", "int", 0.78),
            Col("Sold", "int", 0.68), Col("Service", "int", 0.9), Col("Return", "int", 0.85), Col("Adj.", "int", 0.62),
            Col("Closing", "int", 0.9), Col("Avg cost", "money", 1.1), Col("Sell price", "money", 1.1),
            Col("Sales (net)", "money", 1.25), Col("COGS", "money", 1.15), Col("Gross profit", "money", 1.3)]
    out = []
    tot = dict(opening=0, purchased=0, sold=0, service=0, returned=0, adjusted=0, closing=0, sales=ZERO, cogs=ZERO, gp=ZERO)
    for r in rows:
        out.append([r["name"], r["category"], r["opening"], r["purchased"], r["sold"], r["service"], r["returned"],
                    r["adjusted"], r["closing"], r["avg_cost"], r["selling_price"], r["sales_value"], r["cogs"],
                    r["gross_profit"]])
        for k, src in (("opening", "opening"), ("purchased", "purchased"), ("sold", "sold"), ("service", "service"),
                       ("returned", "returned"), ("adjusted", "adjusted"), ("closing", "closing")):
            tot[k] += r[src]
        tot["sales"] += r["sales_value"]
        tot["cogs"] += r["cogs"]
        tot["gp"] += r["gross_profit"]
    totals = ["TOTAL", "", tot["opening"], tot["purchased"], tot["sold"], tot["service"], tot["returned"],
              tot["adjusted"], tot["closing"], None, None, tot["sales"], tot["cogs"], tot["gp"]]
    return Table(cols, out, totals)


def closing_stock_table(rows: list[dict]) -> Table:
    cols = [Col("Code", width=0.8), Col("Product", width=3.0), Col("Category", width=1.6), Col("Qty", "int", 0.7),
            Col("Avg cost", "money", 1.1), Col("Stock value", "money", 1.3), Col("Sell price", "money", 1.1)]
    data = [[r["code"], r["name"], r["category"], r["closing"], r["avg_cost"], r["stock_value"], r["selling_price"]]
            for r in rows if r["closing"] != 0]
    return Table(cols, data, ["", "TOTAL", "", sum(r[3] for r in data), None, sum((r[5] for r in data), ZERO), None])


def cash_position(d: date, opening_cash) -> dict:
    fig = period_figures(d, d)
    f = fig["f"]
    received = fig["pay_in"]["CASH"]
    expenses = fig["expenses_by_mode"].get("CASH", ZERO)
    refunds = fig["refunds_out"]["CASH"]
    supplier = fig["supplier_out"]["CASH"]
    opening = Decimal(str(opening_cash or 0)).quantize(Decimal("0.01"))
    expected = opening + received - expenses - refunds - supplier
    return dict(opening=opening, received=received, expenses=expenses, refunds=refunds, supplier=supplier,
                expected=expected, fig=fig, f=f)


def build_day_report(d: date, opening_cash, actual_cash, *, status: str = "PREVIEW", carry_forward=None) -> Report:
    cash = cash_position(d, opening_cash)
    fig, f = cash["fig"], cash["f"]
    prows = product_wise(d, d, fig)
    actual = Decimal(str(actual_cash or 0)).quantize(Decimal("0.01"))
    diff = actual - cash["expected"]
    carry = actual if carry_forward is None else Decimal(str(carry_forward)).quantize(Decimal("0.01"))
    active_rows = [r for r in prows if r["purchased"] or r["sold"] or r["service"] or r["returned"] or r["adjusted"]
                   or r["sales_value"]]
    rep = Report(title="Daily Closing Report", subtitle=f"Business date: {d_only(d)}", period=d_only(d),
                 landscape=True, meta=dict(kind="DAY", date=d.isoformat(), status=status))
    rep.sections.append(Section("Summary", [
        KV("Total revenue", f["total_revenue"], strong=True), KV("Net operating profit", f["net_operating_profit"],
                                                                strong=True, tone="good" if f["net_operating_profit"] >= 0 else "bad"),
        KV("Invoices", fig["counts"].get("invoices", 0), "int"), KV("Products sold (qty)", fig["counts"].get("qty_sold", 0), "int"),
        KV("Service jobs completed", fig["counts"].get("jobs_completed", 0), "int"), KV("Expenses", f["expenses"]),
        KV("Cancelled bills", fig["counts"].get("cancelled_bills", 0), "int"), KV("Returns", fig["counts"].get("returns", 0), "int"),
    ], kv_columns=4))
    rep.sections.append(Section("Product-wise report (items with activity today)", table=product_table(active_rows),
                                note="Sold = counter sales; Service = spare parts used in repair jobs; "
                                     "Sales and COGS exclude GST."))
    rep.sections.append(Section("Category report", category_kvs(fig), kv_columns=4))
    rep.sections.append(Section("Service report", service_kvs(fig), kv_columns=4))
    rep.sections.append(Section("Financial report", financial_kvs(f), kv_columns=1))
    rep.sections.append(Section("Payment report", payment_kvs(fig), kv_columns=2))
    rep.sections.append(Section("Cash reconciliation", [
        KV("Opening cash", cash["opening"]), KV("Cash received", cash["received"]),
        KV("Cash expenses", cash["expenses"]), KV("Cash refunds", cash["refunds"]),
        KV("Cash paid to suppliers", cash["supplier"]), KV("Expected closing cash", cash["expected"], strong=True),
        KV("Actual closing cash (counted)", actual, strong=True),
        KV("Cash difference", diff, strong=True, tone="" if diff == 0 else ("good" if diff > 0 else "bad")),
        KV("Cash taken out (bank deposit / owner)", actual - carry),
        KV("Cash kept in drawer for next day", carry, strong=True),
    ], kv_columns=1))
    rep.sections.append(Section("Closing stock (carried forward to next day)", table=closing_stock_table(prows)))
    rep.meta.update(expected_cash=str(cash["expected"]), actual_cash=str(actual), difference=str(diff),
                    opening_cash=str(cash["opening"]), carry_forward=str(carry))
    return rep


def build_period_report(start: date, end: date, title: str = "Monthly Report") -> Report:
    from .parties import total_payables, total_receivables
    fig = period_figures(start, end)
    f, c = fig["f"], fig["counts"]
    prows = product_wise(start, end, fig)
    rec = total_receivables()
    rep = Report(title=title, subtitle=_shop_period(start, end), period=_shop_period(start, end), landscape=True,
                 meta=dict(kind="PERIOD", start=start.isoformat(), end=end.isoformat()))
    closing_qty = sum(r["closing"] for r in prows if r["closing"] > 0)
    closing_val = sum((r["stock_value"] for r in prows), ZERO)
    rep.sections.append(Section("Summary", [
        KV("Total mobile sales", fig["by_group"]["MOBILE"]["net"]), KV("Total accessories sales", fig["by_group"]["ACCESSORY"]["net"]),
        KV("Total spare parts sales", fig["by_group"]["SPARE"]["net"] + f["parts_revenue"]),
        KV("Total service income", f["service_income"]), KV("Total sales revenue", f["total_revenue"], strong=True),
        KV("Total quantity sold", c.get("qty_sold", 0), "int"), KV("Total service jobs", c.get("jobs_completed", 0), "int"),
        KV("Total purchases", f["purchases"]), KV("Cost of goods sold", f["cogs"]),
        KV("Gross profit", f["gross_profit"], strong=True, tone="good" if f["gross_profit"] >= 0 else "bad"),
        KV("Total expenses", f["expenses"]),
        KV("Net operating profit", f["net_operating_profit"], strong=True, tone="good" if f["net_operating_profit"] >= 0 else "bad"),
        KV("Discounts", f["discount_ex"]), KV("Returns", f["returns_taxable"]),
        KV("Customer dues (current)", rec["total"]), KV("Supplier dues (current)", total_payables()),
        KV("Closing stock quantity", closing_qty, "int"), KV("Closing stock valuation", closing_val, strong=True),
    ], kv_columns=4))
    rep.sections.append(Section("Financial statement", financial_kvs(f), kv_columns=1))
    rep.sections.append(Section("Payment breakdown", payment_kvs(fig)))
    rep.sections.append(Section("Product-wise report", table=product_table(prows)))
    rep.sections.append(Section("Service-wise report", service_kvs(fig), kv_columns=4))
    rep.sections.append(Section("Technician-wise performance", table=technician_table(fig)))
    rep.sections.append(Section("Expenses by category", table=Table(
        [Col("Category", width=2), Col("Amount", "money", 1)],
        [[k, v] for k, v in sorted(fig["expenses_by_cat"].items())], ["TOTAL", f["expenses"]])))
    rep.sections.append(Section("Closing stock", table=closing_stock_table(prows)))
    return rep


def technician_table(fig: dict) -> Table:
    cols = [Col("Technician", width=2), Col("Received", "int", 0.8), Col("Completed", "int", 0.8),
            Col("Delivered", "int", 0.8), Col("Labour", "money", 1.1), Col("Parts revenue", "money", 1.2),
            Col("Parts cost", "money", 1.1), Col("Service revenue", "money", 1.2), Col("Profit", "money", 1.1)]
    rows = []
    for name, t in sorted(fig["technicians"].items()):
        rev = t["labour"] + t["parts_rev"]
        rows.append([name, t["received"], t["completed"], t["delivered"], t["labour"], t["parts_rev"], t["parts_cost"],
                     rev, rev - t["parts_cost"]])
    totals = ["TOTAL", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows),
              sum((r[4] for r in rows), ZERO), sum((r[5] for r in rows), ZERO), sum((r[6] for r in rows), ZERO),
              sum((r[7] for r in rows), ZERO), sum((r[8] for r in rows), ZERO)]
    return Table(cols, rows, totals)


def year_bounds(year: int, financial: bool) -> tuple[date, date, str]:
    if financial:
        return date(year, 4, 1), date(year + 1, 3, 31), f"Financial year {year}-{(year + 1) % 100:02d}"
    return date(year, 1, 1), date(year, 12, 31), f"Calendar year {year}"


def build_yearly_report(year: int, financial: bool = False) -> Report:
    start, end, label = year_bounds(year, financial)
    end = min(end, max(start, date.today()))
    rep = build_period_report(start, end, title="Yearly Report")
    rep.subtitle = f"{label} ({_shop_period(start, end)})"
    fig = period_figures(start, end)
    rows = []
    m = start
    while m <= end:
        me = min(_add_month(m, 1) - timedelta(days=1), end)
        dd = [v for d, v in fig["daily"].items() if m <= d <= me]

        def sm(key):
            return sum((v.get(key, ZERO) for v in dd), ZERO)
        rows.append([m.strftime("%b %Y"), sm("net_counter"), sm("service_income"), sm("revenue"), sm("cogs"),
                     sm("gross_profit"), sm("expenses"), sm("net_profit")])
        m = _add_month(m, 1)
    cols = [Col("Month", width=1.2), Col("Counter sales", "money"), Col("Service income", "money"), Col("Revenue", "money"),
            Col("COGS", "money"), Col("Gross profit", "money"), Col("Expenses", "money"), Col("Net profit", "money")]
    totals = ["TOTAL"] + [sum((r[i] for r in rows), ZERO) for i in range(1, 8)]
    rep.sections.insert(1, Section("Month-wise summary", table=Table(cols, rows, totals)))
    rep.meta.update(kind="YEAR", year=year, financial=financial)
    return rep


def build_service_report(start: date, end: date) -> Report:
    fig = period_figures(start, end)
    rep = Report(title="Service Report", subtitle=_shop_period(start, end), period=_shop_period(start, end),
                 meta=dict(kind="SERVICE", start=start.isoformat(), end=end.isoformat()))
    rep.sections.append(Section("Service summary", service_kvs(fig), kv_columns=2))
    rep.sections.append(Section("Technician-wise performance", table=technician_table(fig)))
    return rep


def build_pnl(start: date, end: date) -> Report:
    fig = period_figures(start, end)
    f = fig["f"]
    rep = Report(title="Profit & Loss Statement", subtitle=_shop_period(start, end), period=_shop_period(start, end),
                 meta=dict(kind="PNL"))
    rep.sections.append(Section("Trading account", financial_kvs(f)[:9], kv_columns=1))
    exp = [KV(k, v) for k, v in sorted(fig["expenses_by_cat"].items())]
    rep.sections.append(Section("Operating expenses", exp + [KV("Total operating expenses", f["expenses"], strong=True)]))
    rep.sections.append(Section("Result", [
        KV("Gross profit", f["gross_profit"]), KV("Add: Service labour income", f["labour"]),
        KV("Less: Operating expenses", f["expenses"]),
        KV("Net operating profit", f["net_operating_profit"], strong=True, tone="good" if f["net_operating_profit"] >= 0 else "bad"),
        KV("Spare parts profit (in service)", f["parts_profit"]), KV("Damaged returns written off (at cost, inside COGS)", f["damaged_cost"]),
        KV("Stock adjustments value (not in profit)", f["adjustment_value"])], kv_columns=1))
    return rep


def build_gst_summary(start: date, end: date) -> Report:
    fig = period_figures(start, end)
    f = fig["f"]
    rep = Report(title="GST Summary", subtitle=_shop_period(start, end), period=_shop_period(start, end), meta=dict(kind="GST"))
    out_tax = f["gst_output"]
    in_tax = f["gst_input"]
    rep.sections.append(Section("Output tax (on sales & service)", [
        KV("GST on counter sales", f["sales_gst"]), KV("Less: GST reversed on returns", f["returns_gst"]),
        KV("GST on service (labour + parts)", f["service_gst"]), KV("Total output GST", out_tax, strong=True),
        KV("CGST (50%)", (out_tax / 2).quantize(Decimal("0.01"))), KV("SGST (50%)", out_tax - (out_tax / 2).quantize(Decimal("0.01")))]))
    rep.sections.append(Section("Input tax (on purchases)", [
        KV("GST paid on purchases", f["purchase_gst"]), KV("Input tax credit considered", in_tax, strong=True)]))
    rep.sections.append(Section("Net liability", [KV("Net GST payable (output − input)", out_tax - in_tax, strong=True)],
                                note="" if settings.get("gst_enabled") else
                                "GST is switched off in Settings, so no tax is charged on bills and no input credit is taken."))
    return rep


def build_receivables() -> Report:
    from .parties import _outstanding, list_customers
    from ..db.models import Customer
    rows = []
    today = date.today()
    buckets = [ZERO, ZERO, ZERO, ZERO]
    with session_scope() as s:
        for c in list_customers(with_dues_only=True, include_walk_in=True):
            cust = s.get(Customer, c["id"])
            b = [ZERO, ZERO, ZERO, ZERO]
            for d in _outstanding(s, cust):
                age = (today - d["date"]).days if d["date"] else 999
                idx = 0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3
                b[idx] += d["due"]
            for i in range(4):
                buckets[i] += b[i]
            rows.append([c["name"], c["phone"], c["balance"], *b])
    rep = Report(title="Customer Receivables (Dues)", subtitle=f"As on {d_only(today)}", meta=dict(kind="RECEIVABLES"))
    rep.sections.append(Section("Customer dues with ageing", table=Table(
        [Col("Customer", width=2), Col("Phone", width=1.2), Col("Total due", "money"), Col("0–30 days", "money"),
         Col("31–60 days", "money"), Col("61–90 days", "money"), Col("Over 90 days", "money")], rows,
        ["TOTAL", "", sum((r[2] for r in rows), ZERO), *buckets])))
    return rep


def build_payables() -> Report:
    from .parties import list_suppliers
    rows = [[x["name"], x["phone"], x["total_purchases"], x["paid"], x["due"]] for x in list_suppliers() if x["due"] != 0]
    rep = Report(title="Supplier Payables (Dues)", subtitle=f"As on {d_only(date.today())}", meta=dict(kind="PAYABLES"))
    rep.sections.append(Section("Supplier dues", table=Table(
        [Col("Supplier", width=2), Col("Phone", width=1.2), Col("Purchases", "money"), Col("Paid", "money"),
         Col("Due", "money")], rows,
        ["TOTAL", "", sum((r[2] for r in rows), ZERO), sum((r[3] for r in rows), ZERO), sum((r[4] for r in rows), ZERO)])))
    return rep


def build_cashbook(start: date, end: date) -> Report:
    rows = []
    with session_scope() as s:
        for p in s.scalars(select(Payment).where(Payment.status == "ACTIVE", Payment.business_date >= start,
                                                 Payment.business_date <= end).order_by(Payment.business_date, Payment.id)):
            if p.mode == "EXCHANGE":
                continue
            from .parties import KIND_LABELS
            rows.append([p.business_date, KIND_LABELS.get(p.kind, p.kind), p.reference, MODE_LABELS.get(p.mode, p.mode),
                         p.amount if p.direction == "IN" else ZERO, p.amount if p.direction == "OUT" else ZERO])
        for e in s.scalars(select(Expense).where(Expense.status == "ACTIVE", Expense.business_date >= start,
                                                 Expense.business_date <= end)):
            rows.append([e.business_date, f"Expense – {e.category}", e.description, MODE_LABELS.get(e.payment_mode, e.payment_mode),
                         ZERO, e.amount])
    rows.sort(key=lambda r: r[0])
    rep = Report(title="Cash & Bank Book", subtitle=_shop_period(start, end), period=_shop_period(start, end),
                 landscape=True, meta=dict(kind="CASHBOOK"))
    totals_by_mode = defaultdict(lambda: [ZERO, ZERO])
    for r in rows:
        totals_by_mode[r[3]][0] += r[4]
        totals_by_mode[r[3]][1] += r[5]
    rep.sections.append(Section("Summary by mode", [KV(f"{m} – in", v[0]) for m, v in sorted(totals_by_mode.items())] +
                                [KV(f"{m} – out", v[1]) for m, v in sorted(totals_by_mode.items())] +
                                [KV(f"{m} – net", v[0] - v[1], strong=True) for m, v in sorted(totals_by_mode.items())],
                                kv_columns=3))
    rep.sections.append(Section("All money movements", table=Table(
        [Col("Date", "date", 1), Col("Particulars", width=2), Col("Reference", width=1.5), Col("Mode", width=0.8),
         Col("In", "money"), Col("Out", "money")], rows,
        ["TOTAL", "", "", "", sum((r[4] for r in rows), ZERO), sum((r[5] for r in rows), ZERO)])))
    return rep


def build_stock_valuation() -> Report:
    today = date.today()
    rows = product_wise(today, today)
    rep = Report(title="Stock Valuation", subtitle=f"As on {d_only(today)} (weighted average cost)",
                 meta=dict(kind="STOCK"))
    by_group = defaultdict(lambda: [0, ZERO])
    for r in rows:
        if r["closing"] > 0:
            by_group[r["group"]][0] += r["closing"]
            by_group[r["group"]][1] += r["stock_value"]
    rep.sections.append(Section("By category group", [KV(f"{GROUP_LABELS.get(g, g)} – value", v[1]) for g, v in by_group.items()] +
                                [KV("Total stock value", sum((v[1] for v in by_group.values()), ZERO), strong=True)]))
    rep.sections.append(Section("Stock list", table=closing_stock_table(rows)))
    return rep
