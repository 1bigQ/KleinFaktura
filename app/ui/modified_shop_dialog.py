"""UI dialog for fetching and importing modified-Shop addresses and order drafts."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout

from app.db import repo
from app.db.database import get_setting, set_setting, transaction
from app.importers import modified_shop
from app.services import documents
from app.ui.common import Ctx, button, error, hbox


class ModifiedShopImportDialog(QDialog):
    def __init__(self, parent, ctx: Ctx):
        super().__init__(parent)
        self.ctx = ctx
        self.customers: list[dict] = []
        self.orders: list[dict] = []
        self.setWindowTitle("Adressen und Bestellungen aus modified-Shop")
        self.resize(960, 620)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.url = QLineEdit(get_setting(ctx.con, "modified_shop.url", ""))
        self.url.setPlaceholderText("https://shop.example.de")
        self.username = QLineEdit(get_setting(ctx.con, "modified_shop.username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Wird nicht gespeichert")
        form.addRow("Shop-URL", self.url)
        form.addRow("API-Benutzer", self.username)
        form.addRow("Passwort", self.password)
        root.addLayout(form)
        hint = QLabel("Nur lesender Abruf aus dem Shop. Bestellungen werden als Rechnungsentwürfe übernommen und "
                      "niemals automatisch festgeschrieben. Zugangsdaten werden nicht gespeichert.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addLayout(hbox(button("Adressen abrufen", self.fetch_customers), button("Bestellungen abrufen", self.fetch_orders), None))
        self.tabs = QTabWidget()
        self.customer_table = self._table(["Import", "Shop-ID", "Name", "Adresse", "E-Mail"])
        self.order_table = self._table(["Import", "Bestellnr.", "Datum", "Kunde", "Betrag", "Positionen"])
        self.tabs.addTab(self.customer_table, "Adressen")
        self.tabs.addTab(self.order_table, "Bestellungen")
        root.addWidget(self.tabs, 1)
        root.addLayout(hbox(button("Alle auswählen", self.select_all), button("Keine auswählen", self.select_none), None,
                            button("Auswahl importieren", self.import_selected, primary=True), button("Schließen", self.reject)))

    @staticmethod
    def _table(columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _client(self) -> modified_shop.ModifiedShopClient:
        client = modified_shop.ModifiedShopClient(self.url.text(), self.username.text(), self.password.text())
        client.authenticate()
        set_setting(self.ctx.con, "modified_shop.url", self.url.text().strip())
        set_setting(self.ctx.con, "modified_shop.username", self.username.text().strip())
        return client

    def fetch_customers(self):
        try:
            self.customers = self._client().customers()
            self._fill_customers()
            self.tabs.setCurrentIndex(0)
        except Exception as exc:  # noqa: BLE001
            error(self, "modified-Shop – Adressen", exc)

    def fetch_orders(self):
        try:
            self.orders = self._client().orders()
            self._fill_orders()
            self.tabs.setCurrentIndex(1)
        except Exception as exc:  # noqa: BLE001
            error(self, "modified-Shop – Bestellungen", exc)

    @staticmethod
    def _add_checked_row(table: QTableWidget, index: int, values: list[str]):
        row = table.rowCount()
        table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        check.setCheckState(Qt.Checked)
        check.setData(Qt.UserRole, index)
        table.setItem(row, 0, check)
        for col, value in enumerate(values, start=1):
            table.setItem(row, col, QTableWidgetItem(str(value)))

    def _fill_customers(self):
        self.customer_table.setRowCount(0)
        for index, record in enumerate(self.customers):
            mapped, _ = modified_shop.customer_parts(record)
            customer = record.get("customers") if isinstance(record.get("customers"), dict) else record
            shop_id = customer.get("customers_id", "")
            address = ", ".join(part for part in [mapped.get("street"),
                             " ".join(part for part in [mapped.get("zip"), mapped.get("city")] if part)] if part)
            self._add_checked_row(self.customer_table, index, [str(shop_id), repo.customer_display_name(mapped), address, mapped.get("email", "")])

    def _fill_orders(self):
        self.order_table.setRowCount(0)
        for index, record in enumerate(self.orders):
            order, products = modified_shop.order_parts(record)
            order_id = order.get("orders_id", "")
            customer = " ".join(part for part in [str(order.get("customers_firstname", "")).strip(),
                                                    str(order.get("customers_lastname", "")).strip()] if part)
            total = order.get("order_total", order.get("total", ""))
            self._add_checked_row(self.order_table, index, [str(order_id), str(order.get("date_purchased", ""))[:10],
                                                              customer, str(total), str(len(products))])

    def select_all(self):
        table = self.customer_table if self.tabs.currentIndex() == 0 else self.order_table
        for row in range(table.rowCount()):
            table.item(row, 0).setCheckState(Qt.Checked)

    def select_none(self):
        table = self.customer_table if self.tabs.currentIndex() == 0 else self.order_table
        for row in range(table.rowCount()):
            table.item(row, 0).setCheckState(Qt.Unchecked)

    @staticmethod
    def _selected(table: QTableWidget) -> list[int]:
        return [table.item(row, 0).data(Qt.UserRole) for row in range(table.rowCount())
                if table.item(row, 0).checkState() == Qt.Checked]

    def import_selected(self):
        if self.tabs.currentIndex() == 0:
            self._import_customers()
        else:
            self._import_orders()

    def _import_customers(self):
        created = skipped = projects = 0
        try:
            with transaction(self.ctx.con):
                for index in self._selected(self.customer_table):
                    mapped, addresses = modified_shop.customer_parts(self.customers[index])
                    if not (mapped.get("last_name") or mapped.get("company")) or repo.find_similar_customers(self.ctx.con, mapped):
                        skipped += 1
                        continue
                    customer_id = repo.save_customer(self.ctx.con, mapped)
                    created += 1
                    for number, address in enumerate(addresses, start=1):
                        street = " ".join(part for part in [str(address.get("entry_street_address", "")).strip(),
                                                              str(address.get("entry_suburb", "")).strip()] if part)
                        zip_code = str(address.get("entry_postcode", "")).strip()
                        city = str(address.get("entry_city", "")).strip()
                        if not (street or zip_code or city):
                            continue
                        repo.save_project(self.ctx.con, {"customer_id": customer_id, "name": f"Shop-Adresse {number}",
                            "object_street": street, "object_zip": zip_code, "object_city": city,
                            "notes": "Aus modified-Shop Adressbuch importiert"})
                        projects += 1
        except Exception as exc:  # noqa: BLE001
            error(self, "Adressenimport fehlgeschlagen", exc)
            return
        self.ctx.changed()
        QMessageBox.information(self, "Adressenimport", f"Neue Kunden: {created}\nZusätzliche Adressen: {projects}\nÜbersprungen (Dubletten/unvollständig): {skipped}")

    def _customer_for_order(self, order: dict) -> int:
        email = str(order.get("customers_email_address", "")).strip()
        if email:
            row = self.ctx.con.execute("SELECT id FROM customer WHERE LOWER(email)=LOWER(?) LIMIT 1", (email,)).fetchone()
            if row:
                return int(row[0])
        mapped = {"first_name": str(order.get("customers_firstname", "")).strip(),
                  "last_name": str(order.get("customers_lastname", "")).strip(),
                  "company": str(order.get("delivery_company", order.get("billing_company", ""))).strip(),
                  "street": str(order.get("delivery_street_address", order.get("billing_street_address", ""))).strip(),
                  "zip": str(order.get("delivery_postcode", order.get("billing_postcode", ""))).strip(),
                  "city": str(order.get("delivery_city", order.get("billing_city", ""))).strip(),
                  "country": str(order.get("delivery_country", order.get("billing_country", "DE"))).strip() or "DE",
                  "email": email, "source": "modified_shop"}
        if not (mapped["last_name"] or mapped["company"]):
            raise ValueError("Bestellung enthält keinen verwertbaren Kundennamen.")
        return repo.save_customer(self.ctx.con, mapped)

    def _import_orders(self):
        created = skipped = 0
        try:
            with transaction(self.ctx.con):
                for index in self._selected(self.order_table):
                    record = self.orders[index]
                    order, products = modified_shop.order_parts(record)
                    shop_id = str(order.get("orders_id", ""))
                    if not shop_id:
                        skipped += 1
                        continue
                    marker = f"modified-shop order #{shop_id}"
                    if self.ctx.con.execute("SELECT 1 FROM document WHERE import_note=? LIMIT 1", (marker,)).fetchone():
                        skipped += 1
                        continue
                    lines = modified_shop.order_lines(products)
                    if not lines:
                        skipped += 1
                        continue
                    customer_id = self._customer_for_order(order)
                    doc_date = modified_shop.iso_date(order.get("date_purchased")) or date.today().isoformat()
                    tax_mode = "regel" if any(str(line["tax_rate"]) not in ("", "0", "0.0") for line in lines) else "kleinunternehmer"
                    doc = documents.new_draft(self.ctx.con, "rechnung", customer_id)
                    doc.update(doc_date=doc_date, service_date=doc_date, subject=f"Webshop-Bestellung #{shop_id}",
                               tax_mode=tax_mode, notes_text=(doc.get("notes_text", "") + "\n" + marker).strip())
                    doc_id = documents.save_draft(self.ctx.con, doc, lines)
                    self.ctx.con.execute("UPDATE document SET source='import', import_note=? WHERE id=?", (marker, doc_id))
                    created += 1
        except Exception as exc:  # noqa: BLE001
            error(self, "Bestellungsimport fehlgeschlagen", exc)
            return
        self.ctx.changed()
        QMessageBox.information(self, "Bestellungsimport", f"Rechnungsentwürfe angelegt: {created}\nÜbersprungen: {skipped}\n\n"
                                "Bitte vor dem Festschreiben Bestellung, Lieferadresse, Gesamtbetrag und Steuer prüfen.")


def run_modified_shop_import(parent, ctx: Ctx) -> bool:
    return bool(ModifiedShopImportDialog(parent, ctx).exec())
