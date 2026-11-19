"""Products: catalogue, Excel import/export, IMEI / serial tracking and warranty claims."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QLineEdit, QTabWidget, QVBoxLayout,
                               QWidget)

from ... import config
from ...core import imports, inventory, warranty
from ...core.auth import current_user
from ...output.fmt import inr, qty
from ..bus import changed
from ..dialogs import ProductDialog
from ..widgets import (Card, Col, DataTable, FormDialog, Page, button, confirm, error, hbox, info, label, multiline,
                       run_safely, save_file_dialog)

GROUPS = [("All groups", None), ("Mobile phones", "MOBILE"), ("Accessories", "ACCESSORY"), ("Spare parts", "SPARE")]


def product_columns(show_cost: bool) -> list[Col]:
    cols = [Col("code", "ID", width=70), Col("name", "Product", width=240, bold=True), Col("brand", "Brand", width=100),
            Col("category", "Category", width=130), Col("model_number", "Model", width=100), Col("barcode", "Barcode", width=110)]
    if show_cost:
        cols += [Col("purchase_price", "Purchase", "money", 95), Col("avg_cost", "Avg cost", "money", 95)]
    cols += [Col("selling_price", "Selling", "money", 95), Col("quantity", "Stock", "int", 65,
                                                                color=lambda r: "bad" if r["status"] == "OUT" else ("warn" if r["status"] == "LOW" else None)),
             Col("min_stock", "Min", "int", 50)]
    if show_cost:
        cols.append(Col("stock_value", "Stock value", "money", 110))
    cols += [Col("warranty_months", "Warranty (m)", "int", 90), Col("status", "Stock status", "status", 95)]
    return cols


class ProductsPage(Page):
    subtitle = "Mobile phones, accessories and spare parts"

    def __init__(self, parent=None):
        super().__init__(parent)
        u = current_user()
        self.can_edit = u.can("products_edit")
        self.show_cost = u.can("view_profit")
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        # ---------------- product list
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        card = Card()
        self.table = DataTable(product_columns(self.show_cost), placeholder="Search name, brand, code, model, barcode…",
                               stretch="name")
        self.group = QComboBox()
        for t, g in GROUPS:
            self.group.addItem(t, g)
        self.active = QComboBox()
        self.active.addItems(["Active products", "Inactive products", "All products"])
        self.table.add_top(self.group)
        self.table.add_top(self.active)
        self.group.currentIndexChanged.connect(lambda *_: self.refresh())
        self.active.currentIndexChanged.connect(lambda *_: self.refresh())
        self.table.activated.connect(lambda r: self.edit(r) if self.can_edit else None)
        card.add(self.table)
        btns = []
        if self.can_edit:
            btns += [button("Add product", "primary", "package-plus", self.add),
                     button("Edit", "", "pencil", lambda: self.edit(self.table.selected())),
                     button("Activate / deactivate", "", "eye", self.toggle_active),
                     button("Delete", "danger", "trash-2", self.delete), 12,
                     button("Import Excel", "", "upload", self.import_xlsx),
                     button("Template", "", "file-down", self.template)]
        btns += [button("Export Excel", "", "file-spreadsheet", self.export_xlsx), None]
        card.add(hbox(*btns))
        lay.addWidget(card)
        self.tabs.addTab(w, "Product list")
        # ---------------- IMEI tracking
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card("IMEI / serial numbers", "every phone unit with purchase, sale and warranty")
        self.imei_status = QComboBox()
        for t, v in (("All", None), ("In stock", "IN_STOCK"), ("Sold", "SOLD"), ("Damaged", "DAMAGED"), ("Removed", "REMOVED")):
            self.imei_status.addItem(t, v)
        self.imei_table = DataTable([Col("imei1", "IMEI 1", width=150, bold=True), Col("imei2", "IMEI 2", width=140),
                                     Col("serial_no", "Serial", width=100), Col("product", "Product", width=200),
                                     Col("status", "Status", "status", 90), Col("purchase_date", "Purchased", "date", 100),
                                     Col("sale_date", "Sold", "date", 100), Col("invoice_no", "Invoice", width=140),
                                     Col("customer", "Customer", width=160), Col("warranty", "Warranty", width=210)],
                                    placeholder="Type or scan IMEI / serial / model…", stretch="product")
        self.imei_table.add_top(self.imei_status)
        self.imei_status.currentIndexChanged.connect(lambda *_: self.refresh_imei())
        self.imei_table.search.returnPressed.connect(self.refresh_imei)
        self.imei_table.activated.connect(lambda r: self.history(r["imei1"]))
        c2.add(self.imei_table)
        c2.add(hbox(button("IMEI history", "primary", "history", lambda: self.history()),
                    button("New warranty claim", "", "shield-check", self.new_claim), None))
        l2.addWidget(c2)
        self.tabs.addTab(w2, "IMEI && warranty")
        # ---------------- claims
        w3 = QWidget()
        l3 = QVBoxLayout(w3)
        l3.setContentsMargins(0, 10, 0, 0)
        c3 = Card("Warranty claims")
        self.claims = DataTable([Col("claim_no", "Claim", width=90, bold=True), Col("claim_date", "Date", "date", 100),
                                 Col("imei", "IMEI / serial", width=150), Col("product", "Product", width=180),
                                 Col("customer", "Customer", width=170), Col("issue", "Issue", width=220),
                                 Col("status", "Status", "status", 150), Col("resolution", "Resolution", width=200)],
                                placeholder="Search claims…", stretch="issue")
        self.claims.activated.connect(self.update_claim)
        c3.add(self.claims)
        c3.add(hbox(button("New claim", "primary", "shield-check", self.new_claim),
                    button("Update status", "", "pencil", lambda: self.update_claim(self.claims.selected())), None))
        l3.addWidget(c3)
        self.tabs.addTab(w3, "Warranty claims")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())

    def refresh(self):
        i = self.tabs.currentIndex()
        if i == 0:
            act = self.active.currentIndex()
            rows = inventory.list_products(group=self.group.currentData(), active=True if act == 0 else (False if act == 1 else None))
            self.table.set_rows(rows)
            val = sum((r["stock_value"] for r in rows), Decimal("0"))
            retail = sum((r["retail_value"] for r in rows), Decimal("0"))
            txt = f"{len(rows)} products · {qty(sum(r['quantity'] for r in rows if r['quantity'] > 0))} units in stock"
            if self.show_cost:
                txt += f" · inventory value (cost) {inr(val)} · retail value {inr(retail)}"
            self.table.set_footer(txt)
        elif i == 1:
            self.refresh_imei()
        else:
            self.claims.set_rows(warranty.list_claims())

    def refresh_imei(self):
        self.imei_table.set_rows(warranty.search_imei(self.imei_table.search.text() if len(self.imei_table.search.text()) >= 4 else "",
                                                      self.imei_status.currentData()))

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("new") and self.can_edit:
            self.add()

    def add(self):
        dlg = ProductDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def edit(self, row):
        if not row:
            error(self, "Select a product first.")
            return
        if ProductDialog(self, row["id"]).exec() == QDialog.Accepted:
            self.refresh()

    def toggle_active(self):
        r = self.table.selected()
        if not r:
            return
        run_safely(self, inventory.set_product_active, r["id"], not r["is_active"],
                   success="Product " + ("activated" if not r["is_active"] else "deactivated"), topic="stock")
        self.refresh()

    def delete(self):
        r = self.table.selected()
        if r and confirm(self, "Delete product", f"Delete '{r['name']}'? Only products that were never used can be deleted.",
                         "Delete", danger=True):
            run_safely(self, inventory.delete_product, r["id"], success="Product deleted", topic="stock")
            self.refresh()

    def export_xlsx(self):
        path = save_file_dialog(self, "Export products", str(config.exports_dir() / "Products.xlsx"), "Excel (*.xlsx)")
        if path:
            run_safely(self, imports.export_products, path, success="Products exported")

    def template(self):
        path = save_file_dialog(self, "Save import template", str(config.exports_dir() / "Product_import_template.xlsx"),
                                "Excel (*.xlsx)")
        if path:
            run_safely(self, imports.write_template, path, success="Template saved")

    def import_xlsx(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import products", str(config.exports_dir()), "Excel (*.xlsx)")
        if not path:
            return
        res = run_safely(self, imports.import_products, path, topic="stock")
        if res:
            msg = f"{res['created']} product(s) imported."
            if res["errors"]:
                msg += "\n\nRows skipped:\n" + "\n".join(f"Row {r}: {e}" for r, e in res["errors"][:15])
            info(self, "Import finished", msg)
            self.refresh()

    def history(self, value: str | None = None):
        from ..widgets import ask_text
        value = value or (self.imei_table.selected() or {}).get("imei1") or ask_text(self, "IMEI history", "IMEI or serial number")
        if not value:
            return
        events = run_safely(self, warranty.imei_history, value)
        if events is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"History – {value}")
        dlg.resize(900, 420)
        lay = QVBoxLayout(dlg)
        t = DataTable([Col("date", "Date", "date", 100), Col("event", "Event", width=170, bold=True),
                       Col("product", "Product", width=190), Col("reference", "Reference", width=170),
                       Col("details", "Details", width=300)], search=False, stretch="details")
        t.set_rows(events if events is not True else [])
        lay.addWidget(label(f"IMEI / serial {value}", "SectionTitle"))
        lay.addWidget(t)
        if not events or events is True:
            lay.addWidget(label("No records found for this IMEI / serial number.", "Muted"))
        dlg.exec()

    def new_claim(self):
        dlg = FormDialog(self, "New warranty claim")
        imei = QLineEdit((self.imei_table.selected() or {}).get("imei1", ""))
        issue = multiline("What is the problem?", 70)
        notes = QLineEdit()
        dlg.form.addRow("IMEI / serial *", imei)
        dlg.form.addRow("Issue *", issue)
        dlg.form.addRow("Notes", notes)
        dlg.finish_layout()
        dlg.on_save = lambda: setattr(dlg, "res", warranty.create_claim(imei.text(), issue.toPlainText(), notes.text()))
        if dlg.exec() == QDialog.Accepted:
            info(self, "Claim created", f"Claim {dlg.res['claim_no']} created.\nWarranty: {dlg.res['warranty']}")
            changed("warranty")
            self.refresh()

    def update_claim(self, row):
        if not row:
            return
        dlg = FormDialog(self, f"Update claim {row['claim_no']}")
        st = QComboBox()
        for s in warranty.CLAIM_STATUSES:
            st.addItem(warranty.CLAIM_LABELS[s], s)
        st.setCurrentIndex(max(0, st.findData(row["status"])))
        res = multiline("Resolution", 60)
        res.setPlainText(row.get("resolution", ""))
        dlg.form.addRow("Status", st)
        dlg.form.addRow("Resolution", res)
        dlg.finish_layout()
        dlg.on_save = lambda: warranty.update_claim(row["id"], st.currentData(), res.toPlainText())
        if dlg.exec() == QDialog.Accepted:
            changed("warranty")
            self.refresh()
