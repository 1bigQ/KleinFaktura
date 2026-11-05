"""Read-only client and data mappers for modified-Shop REST API v1."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any


class ModifiedShopError(RuntimeError):
    """API connection, authentication, or response error."""


def _base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ModifiedShopError("Bitte eine HTTPS-Shop-Adresse ohne Zugangsdaten eingeben.")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[:-7]
    return urllib.parse.urlunsplit(("https", parsed.netloc, path, "", "")).rstrip("/")


class ModifiedShopClient:
    """Small stdlib-only client. Credentials and access token are kept in memory only."""

    def __init__(self, shop_url: str, username: str, password: str, timeout: int = 20):
        self.base_url = _base_url(shop_url) + "/api/v1"
        self.username = (username or "").strip()
        self.password = password or ""
        self.timeout = timeout
        self.access_token = ""
        if not self.username or not self.password:
            raise ModifiedShopError("Bitte API-Benutzername und Passwort eingeben.")

    def authenticate(self) -> None:
        request = urllib.request.Request(
            self.base_url + "/oauth",
            headers={"Accept": "application/json", "User-Agent": "KleinFaktura-modified-shop-import/1",
                     "user": self.username, "password": self.password},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ModifiedShopError("Anmeldung fehlgeschlagen. API-Benutzer und Passwort prüfen.") from exc
            raise ModifiedShopError(f"Anmeldung fehlgeschlagen (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ModifiedShopError(f"Shop nicht erreichbar: {exc}") from exc
        self.access_token = str(payload.get("access_token") or "") if isinstance(payload, dict) else ""
        if not self.access_token:
            raise ModifiedShopError("Die Shop-API hat kein Zugriffstoken zurückgegeben.")

    def _request(self, path: str, params: dict | None = None):
        if not self.access_token:
            self.authenticate()
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.access_token}",
                     "User-Agent": "KleinFaktura-modified-shop-import/1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ModifiedShopError("API-Zugriff verweigert. Berechtigungen im modified-Shop prüfen.") from exc
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise ModifiedShopError(f"Shop-API antwortet mit HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ModifiedShopError(f"Shop nicht erreichbar: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModifiedShopError("Die Shop-API hat keine gültige JSON-Antwort geliefert.") from exc

    @staticmethod
    def _rows(payload: Any) -> tuple[list[dict], int | None]:
        if not isinstance(payload, dict):
            raise ModifiedShopError("Unerwartetes Datenformat der Shop-API.")
        if payload.get("errormessage"):
            return [], 0
        rows = payload.get("data", [])
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = []
        paging = payload.get("paging") or {}
        total = paging.get("total")
        try:
            total = int(total) if total is not None else None
        except (TypeError, ValueError):
            total = None
        return [r for r in rows if isinstance(r, dict)], total

    def _list(self, resource: str, with_data: str) -> list[dict]:
        result: list[dict] = []
        page, limit = 1, 100
        while True:
            payload = self._request("/" + resource, {"page": page, "limit": limit, "with": with_data})
            rows, total = self._rows(payload)
            if not rows:
                break
            result.extend(rows)
            if (total is not None and len(result) >= total) or len(rows) < limit:
                break
            page += 1
            if page > 10000:
                raise ModifiedShopError("Abbruch: unerwartet viele API-Seiten.")
        return result

    def customers(self) -> list[dict]:
        return self._list("customers", "address")

    def orders(self) -> list[dict]:
        return self._list("orders", "products,total")


def _str(data: dict | None, *keys: str, default: str = "") -> str:
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return default


def customer_parts(record: dict) -> tuple[dict, list[dict]]:
    """Map a modified-Shop customer record to KleinFaktura and return extra addresses."""
    customer = record.get("customers") if isinstance(record.get("customers"), dict) else record
    addresses = record.get("address_book") or []
    if isinstance(addresses, dict):
        addresses = [addresses]
    addresses = [address for address in addresses if isinstance(address, dict)]
    default_id = _str(customer, "customers_default_address_id", "default_address_id")
    primary = next((a for a in addresses if str(a.get("address_book_id", "")) == default_id), None)
    primary = primary or (addresses[0] if addresses else {})
    mapped = {
        "first_name": _str(primary, "entry_firstname", "firstname", default=_str(customer, "customers_firstname", "first_name")),
        "last_name": _str(primary, "entry_lastname", "lastname", default=_str(customer, "customers_lastname", "last_name")),
        "company": _str(primary, "entry_company", "company", default=_str(customer, "customers_company", "company")),
        "street": " ".join(p for p in [_str(primary, "entry_street_address", "street"), _str(primary, "entry_suburb", "suburb")] if p),
        "zip": _str(primary, "entry_postcode", "postcode", "zip"),
        "city": _str(primary, "entry_city", "city"),
        "country": _str(primary, "countries_iso_code_2", "entry_country", "country", default="DE"),
        "email": _str(customer, "customers_email_address", "email"),
        "phone": _str(customer, "customers_telephone", "phone"),
        "source": "modified_shop",
    }
    return mapped, [a for a in addresses if a is not primary]


def order_parts(record: dict) -> tuple[dict, list[dict]]:
    order = record.get("orders") if isinstance(record.get("orders"), dict) else record
    products = record.get("orders_products") or record.get("products") or []
    if isinstance(products, dict):
        products = [products]
    return order, [p for p in products if isinstance(p, dict)]


def money_to_cents(value: Any) -> int:
    text = str(value or "0").strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return int((Decimal(text) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Ungültiger Geldbetrag aus der Shop-API: {value!r}") from exc


def order_lines(products: list[dict], default_tax_rate: str = "19") -> list[dict]:
    lines = []
    for product in products:
        price = product.get("final_price")
        if price in (None, ""):
            price = product.get("products_price", product.get("price", 0))
        lines.append({
            "title": _str(product, "products_name", "name", "title", default="Shop-Artikel"),
            "description": "",
            "quantity": _str(product, "products_quantity", "quantity", default="1"),
            "unit": "Stück",
            "unit_price_cents": money_to_cents(price),
            "tax_rate": _str(product, "products_tax", "tax_rate", default=default_tax_rate),
        })
    return lines


def iso_date(value: Any) -> str:
    text = str(value or "")[:10]
    return text if len(text) == 10 and text[4] == "-" and text[7] == "-" else ""
