# modified-Shop-Import

Dieser Dialog bietet den read-only Abruf von Adressen und Bestellungen aus der modified-Shop REST API v1. Der Shop stellt dafür `GET /api/v1/customers` (mit `with=address`) und `GET /api/v1/orders` (mit `with=products,total`) bereit.

## Voraussetzungen

- modified-Shop mit installierter/aktivierter API v1
- API-Konto mit Leserechten für Kunden inklusive Adressbuch und Bestellungen
- HTTPS-Shop-URL

## Sicherheit und Verhalten

Das Passwort wird nicht gespeichert und bleibt zusammen mit dem kurzlebigen API-Token nur im Arbeitsspeicher des Dialogs. Shop-Daten werden ausschließlich über GET-Endpunkte abgerufen. Adress-Dubletten werden vorsichtshalber übersprungen; zusätzliche Adressbucheinträge werden als KleinFaktura-Projekte angelegt.

Bestellungen werden als bearbeitbare Rechnungsentwürfe importiert, nie automatisch festgeschrieben. Die Importprüfung dedupliziert anhand der Shop-Bestellnummer. Bitte vor dem Festschreiben die Übernahme der Liefer-/Rechnungsadresse, Steuer, Versandkosten, Rabatte, Gutscheine, Zahlung und Bestellsumme sorgfältig prüfen: diese Shop-Bestandteile werden nicht automatisch als Zahlungen oder gesonderte Positionen verbucht.

## Aufruf aus der Kundenverwaltung

Der Dialog ist als `app.ui.modified_shop_dialog.run_modified_shop_import(parent, ctx)` verfügbar. Eine UI-Aktion in `CustomersPage` kann diesen Aufruf mit einem Button „modified-Shop importieren“ verbinden; siehe `app/ui/customers.py`.
