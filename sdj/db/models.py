"""Relational schema for SDJ Mobile Care.

Design rules
  * Money columns use the exact Money type (integer paise in SQLite).
  * Every stock change is a row in stock_movements; products.quantity is the
    running balance kept in the same transaction. A unique constraint on
    (ref_type, ref_line_id, movement_type) makes double deduction impossible.
  * Financial rows are never deleted (SQLite triggers in init_db enforce this);
    they are cancelled / voided with a reason and an audit-log entry.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Cost, Money


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# ---------------------------------------------------------------- settings / users
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="CASHIER", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(40), default="system")
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    details: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- master data
class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    group: Mapped[str] = mapped_column("grp", String(12), nullable=False)  # MOBILE / ACCESSORY / SPARE
    requires_imei: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_person: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str] = mapped_column(String(20), default="", index=True)
    email: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    gstin: Mapped[str] = mapped_column(String(20), default="")
    opening_balance = mapped_column(Money, default=0, nullable=False)  # amount we owe
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), default="", index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(80), default="")
    gstin: Mapped[str] = mapped_column(String(20), default="")
    opening_balance = mapped_column(Money, default=0, nullable=False)  # old dues brought forward
    notes: Mapped[str] = mapped_column(Text, default="")
    is_walk_in: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(60), default="")
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    model_number: Mapped[str] = mapped_column(String(60), default="")
    barcode: Mapped[str | None] = mapped_column(String(60), unique=True)
    purchase_price = mapped_column(Money, default=0, nullable=False)   # latest purchase price
    selling_price = mapped_column(Money, default=0, nullable=False)
    avg_cost = mapped_column(Cost, default=0, nullable=False)           # weighted average cost
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    warranty_months: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    gst_rate = mapped_column(Money, default=18, nullable=False)
    hsn_code: Mapped[str] = mapped_column(String(12), default="")
    track_imei: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    category: Mapped[Category] = relationship(lazy="joined")
    supplier: Mapped[Supplier | None] = relationship()


class ImeiRecord(Base):
    """One physical serialised unit (phone) – links purchase, sale and warranty."""
    __tablename__ = "imei_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    imei1: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    imei2: Mapped[str] = mapped_column(String(30), default="", index=True)
    serial_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    status: Mapped[str] = mapped_column(String(12), default="IN_STOCK", index=True)
    source: Mapped[str] = mapped_column(String(16), default="PURCHASE")  # OPENING/PURCHASE/ADJUSTMENT
    purchase_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_items.id"))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    purchase_cost = mapped_column(Money, default=0)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    sale_item_id: Mapped[int | None] = mapped_column(ForeignKey("sale_items.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    sale_date: Mapped[date | None] = mapped_column(Date)
    warranty_expiry: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    product: Mapped[Product] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("ref_type", "ref_line_id", "movement_type", name="uq_stock_once"),
        Index("ix_stock_prod_date", "product_id", "business_date"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    qty_change: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_after: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost = mapped_column(Cost, default=0)
    avg_cost_after = mapped_column(Cost, default=0)
    ref_type: Mapped[str] = mapped_column(String(16), default="")
    ref_id: Mapped[int | None] = mapped_column(Integer)
    ref_line_id: Mapped[int | None] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(Integer)

    product: Mapped[Product] = relationship()


# ---------------------------------------------------------------- purchases
class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    client_ref: Mapped[str | None] = mapped_column(String(40), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    supplier_invoice_no: Mapped[str] = mapped_column(String(40), default="")
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subtotal = mapped_column(Money, default=0, nullable=False)
    gst_amount = mapped_column(Money, default=0, nullable=False)
    discount = mapped_column(Money, default=0, nullable=False)
    additional_charges = mapped_column(Money, default=0, nullable=False)
    total = mapped_column(Money, default=0, nullable=False)
    paid_amount = mapped_column(Money, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")

    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list["PurchaseItem"]] = relationship(back_populates="purchase", order_by="PurchaseItem.id")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price = mapped_column(Money, default=0, nullable=False)  # excluding GST
    gst_rate = mapped_column(Money, default=0, nullable=False)
    gst_amount = mapped_column(Money, default=0, nullable=False)
    line_total = mapped_column(Money, default=0, nullable=False)  # incl. GST
    landed_unit_cost = mapped_column(Cost, default=0, nullable=False)
    imei_list: Mapped[str] = mapped_column(Text, default="")

    purchase: Mapped[Purchase] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


# ---------------------------------------------------------------- sales
class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    client_ref: Mapped[str | None] = mapped_column(String(40), unique=True)
    sale_type: Mapped[str] = mapped_column(String(12), default="BILL")  # BILL / QUICK / EXCHANGE
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(100), default="")
    customer_phone: Mapped[str] = mapped_column(String(20), default="")
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    gst_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    price_includes_tax: Mapped[bool] = mapped_column(Boolean, default=True)
    gross_amount = mapped_column(Money, default=0, nullable=False)      # sum qty x price (as entered)
    item_discount = mapped_column(Money, default=0, nullable=False)
    bill_discount = mapped_column(Money, default=0, nullable=False)
    gross_ex_tax = mapped_column(Money, default=0, nullable=False)
    discount_ex_tax = mapped_column(Money, default=0, nullable=False)
    taxable_amount = mapped_column(Money, default=0, nullable=False)
    gst_amount = mapped_column(Money, default=0, nullable=False)
    round_off = mapped_column(Money, default=0, nullable=False)
    grand_total = mapped_column(Money, default=0, nullable=False)
    paid_amount = mapped_column(Money, default=0, nullable=False)       # active receipts
    returned_amount = mapped_column(Money, default=0, nullable=False)   # value of returns (incl. tax)
    refunded_amount = mapped_column(Money, default=0, nullable=False)   # money given back
    initial_due = mapped_column(Money, default=0, nullable=False)       # credit given at billing time
    cogs_total = mapped_column(Money, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="COMPLETED", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(Integer)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")

    customer: Mapped[Customer] = relationship()
    items: Mapped[list["SaleItem"]] = relationship(back_populates="sale", order_by="SaleItem.id")

    @property
    def balance_due(self):
        return (self.grand_total - self.returned_amount) - (self.paid_amount - self.refunded_amount)


class SaleItem(Base):
    __tablename__ = "sale_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(120), default="")
    category_group: Mapped[str] = mapped_column(String(12), default="")
    imei_record_id: Mapped[int | None] = mapped_column(ForeignKey("imei_records.id"))
    imei_text: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price = mapped_column(Money, default=0, nullable=False)
    item_discount = mapped_column(Money, default=0, nullable=False)
    bill_discount_share = mapped_column(Money, default=0, nullable=False)
    gross_amount = mapped_column(Money, default=0, nullable=False)
    gross_ex_tax = mapped_column(Money, default=0, nullable=False)
    discount_ex_tax = mapped_column(Money, default=0, nullable=False)
    taxable_value = mapped_column(Money, default=0, nullable=False)
    gst_rate = mapped_column(Money, default=0, nullable=False)
    gst_amount = mapped_column(Money, default=0, nullable=False)
    line_total = mapped_column(Money, default=0, nullable=False)
    unit_cost = mapped_column(Cost, default=0, nullable=False)
    cost_total = mapped_column(Money, default=0, nullable=False)
    returned_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    returned_value = mapped_column(Money, default=0, nullable=False)
    returned_taxable = mapped_column(Money, default=0, nullable=False)
    warranty_months: Mapped[int] = mapped_column(Integer, default=0)
    warranty_expiry: Mapped[date | None] = mapped_column(Date)

    sale: Mapped[Sale] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Payment(Base):
    """Every rupee that comes in or goes out (except expenses, kept separately)."""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_ref: Mapped[str | None] = mapped_column(String(48), unique=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)        # IN / OUT
    mode: Mapped[str] = mapped_column(String(12), nullable=False)            # CASH/UPI/CARD/OTHER/EXCHANGE
    amount = mapped_column(Money, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # kinds: SALE_RECEIPT, DUE_COLLECTION, SERVICE_ADVANCE, SERVICE_RECEIPT, OPENING_DUE,
    #        REFUND, SERVICE_REFUND, EXCHANGE_CREDIT, SUPPLIER_PAYMENT
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    ref_type: Mapped[str] = mapped_column(String(12), default="")            # SALE/SERVICE/PURCHASE/RETURN/OPENING
    ref_id: Mapped[int | None] = mapped_column(Integer, index=True)
    reference: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(Integer)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    void_reason: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- returns
class SaleReturn(Base):
    __tablename__ = "returns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    client_ref: Mapped[str | None] = mapped_column(String(40), unique=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    return_type: Mapped[str] = mapped_column(String(10), default="PARTIAL")   # FULL / PARTIAL / EXCHANGE
    reason: Mapped[str] = mapped_column(Text, default="")
    total_value = mapped_column(Money, default=0, nullable=False)            # incl. tax (+ round-off on final return)
    taxable_value = mapped_column(Money, default=0, nullable=False)
    gst_value = mapped_column(Money, default=0, nullable=False)
    round_off_value = mapped_column(Money, default=0, nullable=False)
    cost_reversed = mapped_column(Money, default=0, nullable=False)          # restocked items at cost
    damaged_cost = mapped_column(Money, default=0, nullable=False)
    refund_amount = mapped_column(Money, default=0, nullable=False)
    refund_mode: Mapped[str] = mapped_column(String(12), default="")
    exchange_sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    exchange_credit = mapped_column(Money, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)

    sale: Mapped[Sale] = relationship(foreign_keys=[sale_id])
    items: Mapped[list["ReturnItem"]] = relationship(back_populates="ret", order_by="ReturnItem.id")


class ReturnItem(Base):
    __tablename__ = "return_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("returns.id"), nullable=False, index=True)
    sale_item_id: Mapped[int] = mapped_column(ForeignKey("sale_items.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), default="")
    category_group: Mapped[str] = mapped_column(String(12), default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(String(12), default="RESALABLE")  # RESALABLE / DAMAGED
    total_value = mapped_column(Money, default=0, nullable=False)
    taxable_value = mapped_column(Money, default=0, nullable=False)
    gst_value = mapped_column(Money, default=0, nullable=False)
    unit_cost = mapped_column(Cost, default=0, nullable=False)
    cost_total = mapped_column(Money, default=0, nullable=False)
    imei_text: Mapped[str] = mapped_column(String(80), default="")
    restocked: Mapped[bool] = mapped_column(Boolean, default=True)

    ret: Mapped[SaleReturn] = relationship(back_populates="items")


# ---------------------------------------------------------------- expenses
class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="")
    amount = mapped_column(Money, nullable=False)
    payment_mode: Mapped[str] = mapped_column(String(12), default="CASH")
    notes: Mapped[str] = mapped_column(Text, default="")
    client_ref: Mapped[str | None] = mapped_column(String(48), unique=True)
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE", index=True)
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)
    void_reason: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- service / repair
class ServiceJob(Base):
    __tablename__ = "service_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    client_ref: Mapped[str | None] = mapped_column(String(40), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(100), default="")
    customer_phone: Mapped[str] = mapped_column(String(20), default="", index=True)
    brand: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    imei: Mapped[str] = mapped_column(String(40), default="", index=True)
    accessories: Mapped[str] = mapped_column(String(200), default="")
    problem: Mapped[str] = mapped_column(Text, default="")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    technician: Mapped[str] = mapped_column(String(60), default="")
    received_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    estimated_delivery: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="RECEIVED", index=True)
    labour_charge = mapped_column(Money, default=0, nullable=False)
    discount = mapped_column(Money, default=0, nullable=False)
    parts_amount = mapped_column(Money, default=0, nullable=False)       # parts charged (as entered)
    parts_cost = mapped_column(Money, default=0, nullable=False)
    labour_taxable = mapped_column(Money, default=0, nullable=False)
    labour_gst = mapped_column(Money, default=0, nullable=False)
    parts_taxable = mapped_column(Money, default=0, nullable=False)
    parts_gst = mapped_column(Money, default=0, nullable=False)
    total_amount = mapped_column(Money, default=0, nullable=False)
    paid_amount = mapped_column(Money, default=0, nullable=False)        # net of refunds
    completed_date: Mapped[date | None] = mapped_column(Date, index=True)  # income recognised on this day
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_date: Mapped[date | None] = mapped_column(Date, index=True)
    warranty_days: Mapped[int] = mapped_column(Integer, default=0)
    warranty_expiry: Mapped[date | None] = mapped_column(Date)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    items: Mapped[list["ServiceItem"]] = relationship(back_populates="job", order_by="ServiceItem.id")

    @property
    def balance(self):
        return self.total_amount - self.paid_amount


class ServiceItem(Base):
    __tablename__ = "service_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("service_jobs.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price = mapped_column(Money, default=0, nullable=False)
    unit_cost = mapped_column(Cost, default=0, nullable=False)
    line_amount = mapped_column(Money, default=0, nullable=False)
    cost_total = mapped_column(Money, default=0, nullable=False)
    gst_rate = mapped_column(Money, default=0, nullable=False)
    discount_share = mapped_column(Money, default=0, nullable=False)
    taxable_value = mapped_column(Money, default=0, nullable=False)
    gst_amount = mapped_column(Money, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")    # ACTIVE / REMOVED
    client_ref: Mapped[str | None] = mapped_column(String(48), unique=True)
    added_date: Mapped[date] = mapped_column(Date, nullable=False)
    removed_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[ServiceJob] = relationship(back_populates="items")


# ---------------------------------------------------------------- warranty
class WarrantyClaim(Base):
    __tablename__ = "warranty_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    imei_record_id: Mapped[int | None] = mapped_column(ForeignKey("imei_records.id"))
    imei_text: Mapped[str] = mapped_column(String(40), default="", index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(120), default="")
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    customer_name: Mapped[str] = mapped_column(String(100), default="")
    customer_phone: Mapped[str] = mapped_column(String(20), default="")
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    issue: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    resolution: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------- daily closing
class DailyClosing(Base):
    __tablename__ = "daily_closings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="CLOSED")   # CLOSED / REOPENED
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True)
    opening_cash = mapped_column(Money, default=0, nullable=False)
    cash_received = mapped_column(Money, default=0, nullable=False)
    cash_expenses = mapped_column(Money, default=0, nullable=False)
    cash_refunds = mapped_column(Money, default=0, nullable=False)
    cash_supplier_payments = mapped_column(Money, default=0, nullable=False)
    expected_cash = mapped_column(Money, default=0, nullable=False)
    actual_cash = mapped_column(Money, default=0, nullable=False)
    difference = mapped_column(Money, default=0, nullable=False)
    carry_forward = mapped_column(Money, default=0, nullable=False)   # cash left in drawer for next day
    total_revenue = mapped_column(Money, default=0, nullable=False)
    net_sales = mapped_column(Money, default=0, nullable=False)
    gross_profit = mapped_column(Money, default=0, nullable=False)
    net_profit = mapped_column(Money, default=0, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, default="")
    pdf_path: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    closed_by: Mapped[int | None] = mapped_column(Integer)
    closed_by_name: Mapped[str] = mapped_column(String(80), default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    close_count: Mapped[int] = mapped_column(Integer, default=1)
    reopened_by_name: Mapped[str] = mapped_column(String(80), default="")
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime)
    reopen_reason: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- phone link (sync over the shop Wi-Fi)
class SyncDevice(Base):
    """A phone linked with a one-time code. It signs in as one user account and has its own bill series."""
    __tablename__ = "sync_devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    device_no: Mapped[int] = mapped_column(Integer, nullable=False)            # P1, P2 … in bill numbers
    name: Mapped[str] = mapped_column(String(80), default="")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    secret: Mapped[str] = mapped_column(String(100), nullable=False)          # AES-256 key (base64)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", index=True)   # ACTIVE / REMOVED
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    linked_by: Mapped[str] = mapped_column(String(80), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_ip: Mapped[str] = mapped_column(String(60), default="")
    app_version: Mapped[str] = mapped_column(String(20), default="")
    last_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # replay protection
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    removed_by: Mapped[str] = mapped_column(String(80), default="")


class SyncOp(Base):
    """Every entry received from a phone, so a resent entry is never saved twice."""
    __tablename__ = "sync_ops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    op_uid: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("sync_devices.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    made_at: Mapped[datetime | None] = mapped_column(DateTime)                 # on the phone
    business_date: Mapped[date | None] = mapped_column(Date)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(10), default="DONE")            # DONE / FAILED / CONFLICT
    result: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(Text, default="")


class SyncAlert(Base):
    """Something the owner should look at after a phone synced (last piece sold twice, closed day …)."""
    __tablename__ = "sync_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("sync_devices.id"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    ref_type: Mapped[str] = mapped_column(String(12), default="")
    ref_id: Mapped[int | None] = mapped_column(Integer)
    ref_date: Mapped[date | None] = mapped_column(Date)
    payload: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="OPEN", index=True)   # OPEN / RESOLVED
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[str] = mapped_column(String(80), default="")
    resolution: Mapped[str] = mapped_column(Text, default="")

