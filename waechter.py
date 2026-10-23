#!/usr/bin/env python3
"""
Habanos-Wächter
===============

Prüft Online-Shops auf kubanische Zigarren (Habanos) und schickt eine
Push-Nachricht per ntfy, wenn

  * eine Zigarre wieder verfügbar ist, oder
  * ein neues Produkt im Sortiment auftaucht.

Der letzte bekannte Stand wird in ``state.json`` gespeichert. Beim allerersten
Lauf wird nur der Ausgangsbestand erfasst, ohne Alarme zu schicken.

Aufruf:
  python waechter.py              normaler Lauf (Shops prüfen, Alarme senden)
  python waechter.py --dry-run    nur anzeigen, nichts speichern oder senden
  python waechter.py --test-push  eine Test-Benachrichtigung senden
  python waechter.py --status     aktuell verfügbare Artikel auflisten

Umgebungsvariablen:
  NTFY_TOPIC   Name deines ntfy-Themas (Pflicht für Push-Nachrichten)
  NTFY_SERVER  ntfy-Server, Standard: https://ntfy.sh
  NTFY_TOKEN   optionales Zugriffstoken, falls dein Thema geschützt ist
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from html import unescape
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path(__file__).with_name("state.json")
LOG_FILE = Path(__file__).with_name("verlauf.csv")
LOG_COLUMNS = ["zeit_utc", "zeit_berlin", "wochentag", "stunde", "shop", "ereignis",
               "artikel", "packung", "preis", "verfuegbar_stunden", "url", "schluessel"]
ALERT_KINDS = {"wieder_da", "neu", "neu_oder_wieder", "neu_nicht_lieferbar"}
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
PAUSE_BETWEEN_PAGES = 1.5   # Sekunden; schont die Shops
FAILURE_ALERT_AFTER = 6     # nach so vielen Fehlläufen in Folge warnen


class FetchError(Exception):
    """Ein Shop konnte nicht vollständig gelesen werden."""


@dataclass
class Item:
    key: str            # eindeutige ID innerhalb des Shops
    name: str           # Produktname
    pack: str           # Packung, z. B. "25er Kiste" (leer, wenn unbekannt)
    price: str          # Preis als Text, z. B. "11,00 €"
    url: str
    available: bool
    group: str = ""     # fasst Packungen desselben Produkts zusammen


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def euro(value: str) -> str:
    """'34.00' -> '34,00 €'"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return clean(value)
    formatted = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
    })
    return s


def get(session: requests.Session, url: str, allow_404: bool = False) -> str | None:
    """Lädt eine Seite mit bis zu drei Versuchen."""
    last_error = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code == 200:
                return r.text
            last_error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_error = str(e)
        time.sleep(3 * (attempt + 1))
    raise FetchError(f"{url}: {last_error}")


# --------------------------------------------------------------------------
# Parser – reine Funktionen, damit sie ohne Internet testbar sind
# --------------------------------------------------------------------------

def text(value) -> str:
    """Wie clean(), löst aber auch HTML-Entities auf und verträgt None."""
    return clean(unescape(value)) if isinstance(value, str) else ""


def pack_label(value) -> str:
    value = text(value)
    return f"{value}er" if value.isdigit() else value


def pack_from_attributes(attrs: list[dict] | None, detail: dict | None = None) -> str:
    """Packungsangabe einer Variante. Der Shop lässt manche Werte leer (null)."""
    if detail and isinstance(detail.get("variation"), str) and detail["variation"].strip():
        parts = [p.split(":", 1)[-1] for p in detail["variation"].split(",")]
        values = [pack_label(p) for p in parts if text(p)]
        if values:
            return " / ".join(values)
    attrs = [a for a in (attrs or []) if isinstance(a, dict) and text(a.get("value"))]
    preferred = [a for a in attrs if "verpack" in text(a.get("name")).lower()]
    chosen = preferred or attrs
    return " / ".join(pack_label(a["value"]) for a in chosen)


def money(amount: str | int | None, minor_unit: int | None = 2) -> str:
    try:
        return euro(str(int(amount) / 10 ** int(minor_unit if minor_unit is not None else 2)))
    except (TypeError, ValueError):
        return ""


def parse_zigarrentraum_api(products: list[dict], variations: list[dict],
                            brand_prefix: bool = False) -> list[Item]:
    """Daten aus der WooCommerce-Schnittstelle (zigarrentraum.de, nobbis-tabakwelt.de, pipehouse.de).

    ``brand_prefix``: Marke aus 'brands' voranstellen (Pipe House nennt sie nicht im Titel).

    ``products``: alle Produkte der Kategorie, ``variations``: die Packungsvarianten
    aller Produkte, die als vorrätig gemeldet sind. Ist ein Produkt nicht vorrätig,
    ist es auch keine seiner Packungen; dafür braucht es keinen Abruf.
    Ein Eintrag je Packungsvariante, Schlüssel = Varianten-ID des Shops.
    """
    by_id = {v["id"]: v for v in variations}
    items: list[Item] = []
    for p in products:
        name = text(p.get("name"))
        brands = [text(b.get("name")) for b in p.get("brands") or [] if isinstance(b, dict)]
        if brand_prefix and brands and brands[0] and brands[0].lower() not in name.lower():
            name = f"{brands[0]} {name}"
        url = p.get("permalink") or ""
        minor = (p.get("prices") or {}).get("currency_minor_unit", 2)
        group = str(p["id"])
        if p.get("variations"):
            for v in p["variations"]:
                detail = by_id.get(v["id"])
                pack = pack_from_attributes(v.get("attributes"), detail)
                if detail is not None:
                    available = bool(detail.get("is_in_stock"))
                    price = money((detail.get("prices") or {}).get("price"), minor)
                elif p.get("is_in_stock"):
                    raise FetchError(f"Varianten von '{name}' fehlen in der Antwort")
                else:
                    available, price = False, ""
                items.append(Item(key=str(v["id"]), name=name, pack=pack, price=price,
                                  url=url, available=available, group=group))
        else:
            items.append(Item(key=group, name=name, pack="",
                              price=money((p.get("prices") or {}).get("price"), minor),
                              url=url, available=bool(p.get("is_in_stock")), group=group))
    return items


def parse_casabenden_lieferbar(html: str, base: str = "https://www.casabenden.de") -> tuple[list[Item], int | None]:
    """Liste 'nur sofort lieferbare Artikel' von casabenden.de und cigarworld.de (gleiches System).

    Gibt die Artikel und die vom Shop gemeldete Gesamtzahl zurück.
    """
    soup = BeautifulSoup(html, "html.parser")
    m = re.search(r"(\d+)\s+Artikel gefunden", soup.get_text(" "))
    total = int(m.group(1)) if m else None
    items: list[Item] = []
    # Nur die eigentliche Trefferliste: CIGARWORLD zeigt zusätzlich ein Empfehlungs-Karussell
    links = soup.select("div.search-result a.search-result-item-inner") or \
        soup.select("a.search-result-item-inner")
    for a in links:
        href = a.get("href", "")
        brand = clean(a.select_one(".brand").get_text() if a.select_one(".brand") else "")
        name = clean(a.select_one(".name").get_text() if a.select_one(".name") else "")
        remark = clean(a.select_one(".remark").get_text() if a.select_one(".remark") else "")
        full = " ".join(p for p in (brand, name) if p) or clean(a.get("title"))
        if remark:
            full += f" ({remark})"
        price_el = a.select_one("[data-eurval]")
        price = euro(price_el["data-eurval"]) if price_el else ""
        if a.select_one(".price-remark"):
            price = f"ab {price}"
        variants = a.select_one(".countvalue")
        pack = f"{clean(variants.get_text())} Varianten" if variants else ""
        badge = clean(a.select_one(".product-badge").get_text()) if a.select_one(".product-badge") else ""
        if badge:
            pack = f"{pack}, {badge}" if pack else badge
        avail = a.select_one(".item-availability")
        available = True
        if avail is not None:
            # item-availability--1 = sofort verfügbar
            available = any(c in ("item-availability--1", "item-availability--2")
                            for c in avail.get("class", []))
        items.append(Item(key=href.split("?")[0], name=full, pack=pack, price=price,
                          url=urljoin(base, href), available=available,
                          group=href.split("?")[0]))
    return items, total


def parse_brinkmann(html: str) -> list[Item]:
    """Kategorieseite von brinkmannfinest.de (Shopware). Gelistet = kaufbar."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for box in soup.select("div.product--box"):
        sku = box.get("data-ordernumber", "")
        title_el = box.select_one("a.product--title")
        if not title_el:
            continue
        name = clean(title_el.get("title") or title_el.get_text())
        url = title_el.get("href", "")
        unit = [s for s in box.select(".price--unit .is--nowrap")
                if "price--label" not in (s.get("class") or [])]
        pack = clean(unit[0].get_text()) if unit else ""
        price_el = box.select_one(".price--default")
        price = clean(price_el.get_text() if price_el else "")
        buyable = box.select_one(".buybox--button") is not None
        items.append(Item(key=sku or url, name=name, pack=pack, price=price, url=url,
                          available=buyable, group=sku or url))
    return items


def _price(el) -> str:
    """Preistext normalisieren: '21,60 €*' -> '21,60 €'."""
    t = clean(el.get_text(" ") if el is not None else "").replace("*", "").strip()
    return re.sub(r"\s*€", " €", t).strip()


def _top_level(elements):
    """Nur äußerste Treffer (verschachtelte Boxen ignorieren)."""
    ids = {id(e) for e in elements}
    return [e for e in elements if not any(id(p) in ids for p in e.parents)]


def parse_noblego(html: str) -> tuple[list[Item], int | None]:
    """Kategorieseite von noblego.de (Magento). Ein Eintrag je Packung."""
    soup = BeautifulSoup(html, "html.parser")
    total = None
    for el in soup.select(".amount"):
        m = re.search(r"(\d+)\s+Artikel", clean(el.get_text()))
        if m:
            total = int(m.group(1))
            break
    items: list[Item] = []
    for li in soup.select("ul.products-grid > li.item"):
        info = li.select_one(".product-item-info")
        link = li.select_one("h2.product-name a")
        if not info or not link:
            continue
        name = clean(link.get("title") or link.get_text())
        url = link.get("href", "")
        packs = li.select("ul.product-prices > li")
        if packs:
            for p in packs:
                pack_el = p.select_one('span[title="Verpackungseinheit"]')
                pack = clean(pack_el.get_text() if pack_el else "")
                icon = p.select_one(".availability-icon")
                classes = icon.get("class", []) if icon else []
                available = any(c.endswith("-instock") for c in classes)
                items.append(Item(key=f"{url}#{pack}", name=name, pack=pack,
                                  price=_price(p.select_one(".product-price .price")),
                                  url=url, available=available, group=url))
        else:
            available = any(c.endswith("-instock") for c in info.get("class", []))
            unit = info.select_one(".price-unit")
            items.append(Item(key=url, name=name, pack=clean(unit.get_text()).strip("()") if unit else "",
                              price=_price(info.select_one(".price-box .price")),
                              url=url, available=available, group=url))
    return items, total


def parse_zigarrede(html: str) -> tuple[list[Item], int | None]:
    """Kategorieseite von zigarre.de (Magento 2). Ein Eintrag je Produkt."""
    soup = BeautifulSoup(html, "html.parser")
    amount = soup.select_one(".toolbar-amount")
    m = re.search(r"von\s+(\d+)", clean(amount.get_text()) if amount else "")
    total = int(m.group(1)) if m else None
    items: list[Item] = []
    for li in soup.select("div.products-list ol.product-items > li"):
        link = li.select_one("a.product-item-link")
        if not link:
            continue
        pid_el = li.select_one("[data-product-id]")
        url = link.get("href", "")
        price = _price(li.select_one(".price"))
        if li.select_one(".price-label") and "ab" in clean(li.select_one(".price-label").get_text()).lower():
            price = f"ab {price}"
        items.append(Item(key=pid_el.get("data-product-id") if pid_el else url,
                          name=clean(link.get_text()), pack="", price=price, url=url,
                          available=li.select_one(".stock.unavailable") is None,
                          group=url))
    return items, total


def parse_shopware_listing(html: str) -> list[Item]:
    """Shopware-6-Kategorieseite (z. B. wolsdorff.shop). Ein Eintrag je Artikel/Packung."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for box in soup.select("div.product-box"):
        try:
            info = json.loads(box.get("data-product-information") or "{}")
        except ValueError:
            info = {}
        link = box.select_one("a.product-name")
        if not link:
            continue
        url = link.get("href", "")
        name = clean(link.get("title") or link.get_text()) or text(info.get("name"))
        pid_input = box.select_one('input[name="product-id"]')
        key = info.get("id") or (pid_input.get("value") if pid_input else "") or url
        sold_out = (box.select_one(".out-of-stock-information, .badge-out-of-stock") is not None
                    or "is-out-of-stock" in (box.get("class") or []))
        items.append(Item(key=key, name=name, pack="",
                          price=_price(box.select_one(".product-price")), url=url,
                          available=not sold_out, group=key))
    return items


def parse_tabacstore(html: str) -> list[Item]:
    """tabacstore.de, Liste 'sofort verfügbar' (Shopware 6 mit Packungsauswahl)."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for box in soup.select("div.product-box"):
        link = box.select_one("a.product-name")
        if not link:
            continue
        name = clean(link.get_text())
        url = link.get("href", "")
        group = url
        cfg = box.select_one('input[name="maxia-configurator-config"]')
        if cfg is not None:
            try:
                group = json.loads(cfg.get("value") or "{}").get("parentProductId") or url
            except ValueError:
                pass
        shown_price = _price(box.select_one(".product-price"))
        options = box.select("a.product-detail-configurator-option")
        if not options:
            items.append(Item(key=url, name=name, pack="", price=shown_price, url=url,
                              available=True, group=group))
            continue
        for opt in options:
            label = opt.select_one("label")
            pack = clean(label.get("title") if label and label.get("title") else
                         (label.get_text() if label else ""))
            unavailable = "nicht verfügbar" in clean(label.get_text() if label else "").lower()
            href = opt.get("href", "")
            items.append(Item(key=href or f"{url}#{pack}", name=name, pack=pack,
                              price=shown_price if href == url else "",
                              url=href or url, available=not unavailable, group=group))
    return items


def parse_jtl_list(html: str) -> list[Item]:
    """JTL-Shop (NOVA), Liste nur lieferbarer Artikel, z. B. dalayzigarren.de, starkezigarren.de."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    # Nur die Artikelliste, keine Slider (StarkeZigarren zeigt dort auch ausverkaufte Ware)
    boxes = soup.select("#product-list div.productbox") or soup.select("div.productbox")
    for box in _top_level(boxes):
        link = box.select_one(".productbox-title a")
        if not link:
            continue
        url = link.get("href", "")
        price = re.sub(r"^ab\s+", "", _price(box.select_one(".productbox-price")))
        if box.select_one(".pricestarting"):
            price = f"ab {price}"
        ribbon = box.select_one(".ribbon")
        note = clean(ribbon.get_text()) if ribbon else ""
        pack = "nur einzelne Packungen" if "variation" in note.lower() else ""
        items.append(Item(key=url, name=clean(link.get_text()), pack=pack, price=price,
                          url=url, available=True, group=url))
    return items


def parse_paulbugge(html: str) -> list[Item]:
    """paul-bugge.com, Kategorie Kuba. Punkt rot = nicht lieferbar, grün = lieferbar."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for card in soup.select("div.row-cols-2 > div.col"):
        link = card.select_one("a[href]")
        name_el = card.select_one("[itemprop=name]")
        if not link or not name_el:
            continue
        href = link["href"]
        sub = card.select_one(".pb_OSSB12")
        dot = card.select_one("i.fa-circle")
        color = (dot.get("style", "") if dot else "").lower()
        items.append(Item(key=href, name=clean(name_el.get_text()),
                          pack=clean(sub.get_text()) if sub else "",
                          price=_price(card.select_one(".pb_OSB24")),
                          url=urljoin("https://www.paul-bugge.com", href),
                          available=bool(dot) and "red" not in color, group=href))
    return items


def parse_oxid(html: str) -> list[Item]:
    """OXID-Shop cigarrenversand24.de. Schlüssel = Artikel-ID (gleich in allen Kategorien)."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for box in soup.select("#productList .productData"):
        link = box.select_one("a.title")
        anid = box.select_one("input[name=anid]")
        if not link:
            continue
        key = anid["value"] if anid else link.get("href", "")
        flag = box.select_one(".stockFlag")
        status = clean(flag.get("title", "")).lower() if flag else ""
        price = _price(box.select_one(".price .lead"))
        items.append(Item(key=key, name=clean(link.get_text()), pack="", price=price,
                          url=link.get("href", ""),
                          available=status == "lieferbar", group=key))
    return items


def parse_virtuemart(html: str, base: str) -> tuple[list[Item], list[str]]:
    """VirtueMart-Kategorie (tabak-kontor.de): Artikel und Links zu Unterkategorien."""
    soup = BeautifulSoup(html, "html.parser")
    subs = [urljoin(base, a["href"]) for a in soup.select(".category-view .vm-categories a[href]")
            if a["href"] not in ("#", "")]
    items: list[Item] = []
    for row in soup.select(".browse-view .product-container"):
        link = row.select_one("h2 a")
        price_box = row.select_one("[id^=productPrice]")
        if not link:
            continue
        key = price_box["id"].replace("productPrice", "") if price_box else link.get("href", "")
        available = (row.select_one("button[name=addtocart]") is not None
                     and "nicht verfügbar" not in row.get_text(" ").lower())
        items.append(Item(key=key, name=clean(link.get_text()), pack="",
                          price=_price(row.select_one("span.PricesalesPrice")),
                          url=urljoin(base, link.get("href", "")), available=available, group=key))
    return items, subs


def parse_falkum(html: str) -> tuple[list[Item], int | None]:
    """falkum.de, Kategorie Habanos (Magento mit Hyvä-Oberfläche)."""
    soup = BeautifulSoup(html, "html.parser")
    total = None
    amount = soup.select_one(".toolbar-amount")
    if amount:
        m = re.search(r"(\d+)\s+Artikel", clean(amount.get_text()))
        total = int(m.group(1)) if m else None
    items: list[Item] = []
    for box in _top_level(soup.select(".product-item")):
        link = box.select_one("a.product-item-link")
        if not link:
            continue
        url = link.get("href", "")
        stock = box.select_one(".stock")
        classes = stock.get("class", []) if stock else []
        available = "available" in classes and "unavailable" not in classes
        key = box.get("data-sku") or url
        items.append(Item(key=key, name=clean(link.get_text()), pack="",
                          price=_price(box.select_one(".price")), url=url,
                          available=available, group=key))
    return items, total


def parse_shopify(data: dict, base: str, brand_from_vendor: bool = False) -> list[Item]:
    """Shopify-Schnittstelle /collections/<name>/products.json (c-cigars.de, lagalana.de,
    zigarren-herzog.com). Ein Eintrag je Variante (Packung).

    brand_from_vendor: Marke aus 'vendor' voranstellen (Herzog nennt sie nicht im Titel).
    """
    items: list[Item] = []
    for p in data.get("products") or []:
        name = text(p.get("title"))
        vendor = text(p.get("vendor"))
        if brand_from_vendor and vendor and vendor.lower() not in name.lower():
            name = f"{vendor} {name}"
        url = f"{base}/products/{p.get('handle', '')}"
        for v in p.get("variants") or []:
            pack = text(v.get("title"))
            if pack.lower() == "default title":
                pack = ""
            try:
                price = euro(str(float(v.get("price"))))
            except (TypeError, ValueError):
                price = ""
            items.append(Item(key=str(v.get("id")), name=name, pack=pack, price=price,
                              url=f"{url}?variant={v.get('id')}" if pack else url,
                              available=bool(v.get("available")), group=str(p.get("id"))))
    return items


def parse_epages(data: dict) -> list[Item]:
    """ePages-Schnittstelle /rs/shops/<id>/products (habanos-zigarren.com)."""
    items: list[Item] = []
    for p in data.get("items") or []:
        price = ((p.get("priceInfo") or {}).get("price") or {}).get("formatted") or ""
        url = (p.get("sfUrl") or "").replace("http://", "https://", 1)
        items.append(Item(key=p.get("productId") or url, name=text(p.get("name")), pack="",
                          price=clean(price), url=url, available=bool(p.get("forSale")),
                          group=p.get("productId") or url))
    return items


def parse_odoo(html: str, base: str = "https://www.dominiquelondon.de") -> list[Item]:
    """Odoo-Shop (dominiquelondon.de). Zeigt keinen Lagerstand, daher nur für Neuheiten."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    for box in soup.select(".oe_product"):
        link = box.select_one(".o_wsale_products_item_title a")
        if not link:
            continue
        tpl = box.select_one('input[name="product_template_id"]')
        url = urljoin(base, link.get("href", "")).split("?")[0]
        value = box.select_one(".oe_currency_value")
        key = tpl.get("value") if tpl else url
        items.append(Item(key=key, name=clean(link.get("content") or link.get_text()), pack="",
                          price=f"{clean(value.get_text())} €" if value else "", url=url,
                          available=True, group=key))
    return items


# --------------------------------------------------------------------------
# Shops
# --------------------------------------------------------------------------

@dataclass
class Shop:
    id: str
    name: str
    # "voll": Liste enthält auch nicht lieferbare Artikel
    # "nur_lieferbar": was fehlt, ist nicht lieferbar
    # "nur_neu": kein Lagerstand sichtbar, nur neue Artikel werden gemeldet
    mode: str
    fetch: Callable[[requests.Session], list[Item]]


def get_json(session: requests.Session, url: str) -> tuple:
    """Lädt JSON mit bis zu drei Versuchen und gibt Daten und Header zurück."""
    last_error = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30, headers={"Accept": "application/json"})
            if r.status_code == 200:
                return r.json(), r.headers
            last_error = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last_error = str(e)
        time.sleep(3 * (attempt + 1))
    raise FetchError(f"{url}: {last_error}")


def fetch_zigarrentraum(session: requests.Session) -> list[Item]:
    # Die Schnittstelle sortiert stabil nach ID. Die normalen Kategorieseiten zeigen
    # beim Blättern teils Produkte doppelt und lassen andere weg.
    return fetch_woo(session, "https://www.zigarrentraum.de", "kubanische-zigarren")


def make_woo_fetcher(site: str, category: str, brand_prefix: bool = False,
                     trust_total: bool = True) -> Callable[[requests.Session], list[Item]]:
    return lambda session: fetch_woo(session, site, category, brand_prefix, trust_total)


def fetch_woo(session: requests.Session, site: str, category: str,
              brand_prefix: bool = False, trust_total: bool = True) -> list[Item]:
    """WooCommerce Store API. trust_total=False, wenn der Shop X-WP-Total falsch meldet
    (Pipe House liefert dort immer 1); dann wird geblättert, bis eine Seite leer ist."""
    api = f"{site}/wp-json/wc/store/v1/products"
    products: dict[int, dict] = {}
    total = None
    for page in range(1, 31):
        data, headers = get_json(session, f"{api}?category={category}&per_page=100"
                                          f"&page={page}&orderby=id&order=asc")
        if total is None and trust_total:
            total = int(headers.get("X-WP-Total", 0))
        if not data:
            break
        new = [p for p in data if p["id"] not in products]
        for p in data:
            products[p["id"]] = p
        if not new or (total and len(products) >= total) or len(data) < 100:
            break
        time.sleep(PAUSE_BETWEEN_PAGES)
    if not products:
        raise FetchError("Keine Produkte gefunden (Schnittstelle geändert?)")
    if total and len(products) < total:
        raise FetchError(f"Nur {len(products)} von {total} Produkten gelesen")

    in_stock = [pid for pid, p in products.items() if p.get("is_in_stock") and p.get("variations")]
    variations: list[dict] = []
    for i in range(0, len(in_stock), 20):
        parents = "&".join(f"parent[]={pid}" for pid in in_stock[i:i + 20])
        for page in range(1, 11):
            data, headers = get_json(session, f"{api}?type=variation&{parents}&per_page=100&page={page}")
            variations.extend(data)
            if page >= int(headers.get("X-WP-TotalPages", 1) or 1):
                break
        time.sleep(PAUSE_BETWEEN_PAGES)
    return parse_zigarrentraum_api(list(products.values()), variations, brand_prefix)


def fetch_casabenden(session: requests.Session) -> list[Item]:
    return fetch_benden_system(session, "https://www.casabenden.de")


def fetch_cigarworld(session: requests.Session) -> list[Item]:
    return fetch_benden_system(session, "https://www.cigarworld.de")


def fetch_benden_system(session: requests.Session, site: str) -> list[Item]:
    """Casa Benden und CIGARWORLD laufen auf demselben Shopsystem."""
    base = site + "/zigarren/kuba?nur_lieferbar=1&shoporder=Relevanz&von={}"
    items: list[Item] = []
    total = None
    offset = 0
    for _ in range(20):
        html = get(session, base.format(offset))
        page_items, page_total = parse_casabenden_lieferbar(html, site)
        if total is None:
            total = page_total
            if total is None:
                raise FetchError("Artikelanzahl nicht gefunden (Seitenaufbau geändert?)")
        new = [i for i in page_items if i.key not in {x.key for x in items}]
        items.extend(new)
        if not new or len(items) >= total:
            break
        offset += len(page_items)
        time.sleep(PAUSE_BETWEEN_PAGES)
    if total and len(items) < total:
        raise FetchError(f"Nur {len(items)} von {total} Artikeln gelesen")
    return items


def fetch_brinkmann(session: requests.Session) -> list[Item]:
    base = "https://brinkmannfinest.de/zigarren/herkunft/kuba/?n=48&p={}"
    items: list[Item] = []
    for page in range(1, 21):
        html = get(session, base.format(page))
        page_items = parse_brinkmann(html)
        new = [i for i in page_items if i.key not in {x.key for x in items}]
        if not new:
            if page == 1 and "product--box" not in html and "listing" not in html:
                raise FetchError("Keine Produktliste gefunden (Seitenaufbau geändert?)")
            break
        items.extend(new)
        time.sleep(PAUSE_BETWEEN_PAGES)
    return items


def collect_pages(session: requests.Session, url_for_page: Callable[[int], str],
                  parse: Callable[[str], tuple[list[Item], int | None] | list[Item]],
                  max_pages: int = 40) -> tuple[list[Item], int | None]:
    """Blättert, bis eine Seite nichts Neues mehr bringt (viele Shops liefern dann
    die letzte Seite erneut aus)."""
    items: dict[str, Item] = {}
    total = None
    for page in range(1, max_pages + 1):
        html = get(session, url_for_page(page), allow_404=page > 1)
        if html is None:
            break
        result = parse(html)
        page_items, page_total = result if isinstance(result, tuple) else (result, None)
        if total is None:
            total = page_total
        new = [i for i in page_items if i.key not in items]
        for i in new:
            items[i.key] = i
        if not new:
            break
        time.sleep(PAUSE_BETWEEN_PAGES)
    return list(items.values()), total


def fetch_noblego(session: requests.Session) -> list[Item]:
    base = "https://www.noblego.de/kubanische-zigarren/"
    items, total = collect_pages(session, lambda p: base if p == 1 else f"{base}?p={p}", parse_noblego)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    products = {i.group for i in items}
    if total and len(products) < total:
        raise FetchError(f"Nur {len(products)} von {total} Artikeln gelesen")
    return items


def fetch_zigarrede(session: requests.Session) -> list[Item]:
    base = "https://www.zigarre.de/kubanische-zigarren.html?product_list_limit=96"
    items, total = collect_pages(session, lambda p: f"{base}&p={p}", parse_zigarrede)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    if total and len(items) < total:
        raise FetchError(f"Nur {len(items)} von {total} Artikeln gelesen")
    return items


def fetch_wolsdorff(session: requests.Session) -> list[Item]:
    base = "https://wolsdorff.shop/zigarren/nach-herkunft/kuba/"
    items, _ = collect_pages(session, lambda p: f"{base}?order=name-asc&p={p}", parse_shopware_listing)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    return items


def fetch_tabacstore(session: requests.Session) -> list[Item]:
    base = "https://www.tabacstore.de/kubanische-zigarren/?immediately-available=1&order=zuletzt-hinzugefugt"
    items, _ = collect_pages(session, lambda p: f"{base}&p={p}", parse_tabacstore)
    return items


ACCESSORY_WORDS = re.compile(
    r"feuerzeug|humidor|etui|cutter|abschneider|aschenbecher|ascher|bohrer|tasche|"
    r"zigarrenschere|anzünder|dupont|zubehör|accessoire|porzellanjar|maxitubo|\bleer\b|"
    r"\bbuch\b|shirt|\bpolo\b|\bcap\b|mütze|welt der habanos|porzellantopf|^habanos jar", re.I)
# Humidore, Jars und Etuis mit Zigarren darin sind keine Zubehörartikel
# (z. B. "mit 50 Zigarren", "5er Metalletui", "25er Glass-Jar")
WITH_CIGARS = re.compile(r"mit\s+\d+\s+zigarren|\b\d+er[\s-]*(metall-?etui|glass?-?jar|jar)", re.I)
# Marken, die keine Havannas sind, aber in Kuba-Kategorien auftauchen (z. B. La Galana)
NON_HABANOS = re.compile(r"vegafina|vega fina|maria mancini", re.I)


def is_accessory(item: Item) -> bool:
    """Zubehör (Feuerzeuge, Humidore, Etuis, leere Jars ...) statt Zigarren."""
    return bool(ACCESSORY_WORDS.search(item.name)) and not WITH_CIGARS.search(item.name)


def is_wanted(item: Item) -> bool:
    """Nur Havanna-Zigarren: kein Zubehör, keine Fremdmarken. Gilt für alle Shops."""
    return not is_accessory(item) and not NON_HABANOS.search(item.name)


def make_jtl_fetcher(base: str) -> Callable[[requests.Session], list[Item]]:
    def fetch(session: requests.Session) -> list[Item]:
        items, _ = collect_pages(
            session, lambda p: (base if p == 1 else f"{base}_s{p}") + "?availability=1", parse_jtl_list)
        return items
    return fetch


def fetch_peterheinrichs(session: requests.Session) -> list[Item]:
    # alphabetisch sortiert: bei der Standardsortierung fehlen beim Blättern Artikel
    base = "https://www.peterheinrichs.de/zigarren/kuba/?order=name-asc"
    items, _ = collect_pages(session, lambda p: f"{base}&p={p}", parse_shopware_listing)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    return items


def fetch_falkum(session: requests.Session) -> list[Item]:
    html = get(session, "https://www.falkum.de/zigarren/habanos.html?product_list_limit=all")
    items, total = parse_falkum(html)
    items = dedupe(items)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    if total and len(items) < total:
        raise FetchError(f"Nur {len(items)} von {total} Artikeln gelesen")
    return items


def make_shopify_fetcher(base: str, collection: str,
                         brand_from_vendor: bool = False) -> Callable[[requests.Session], list[Item]]:
    def fetch(session: requests.Session) -> list[Item]:
        items: list[Item] = []
        for page in range(1, 21):
            data, _ = get_json(session, f"{base}/collections/{collection}/products.json"
                                        f"?limit=250&page={page}")
            if not data.get("products"):
                break
            items.extend(parse_shopify(data, base, brand_from_vendor))
            time.sleep(PAUSE_BETWEEN_PAGES)
        items = dedupe(items)
        if not items:
            raise FetchError("Keine Produkte gefunden (Schnittstelle geändert?)")
        return items
    return fetch


def fetch_paulbugge(session: requests.Session) -> list[Item]:
    base = "https://www.paul-bugge.com/kubanische-zigarren"
    items, _ = collect_pages(session, lambda p: base if p == 1 else f"{base}?page={p}", parse_paulbugge)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    return items


def fetch_cigarrenversand24(session: requests.Session) -> list[Item]:
    # 'Habanos-Specialist' ist die volle Liste mit Lagerstand, aber nicht ganz vollständig;
    # 'Unsere verfügbaren Habanos' pflegt der Shop von Hand. Beides zusammen.
    site = "https://www.cigarrenversand24.de"
    full, _ = collect_pages(
        session, lambda p: f"{site}/Nach-Marke/Habanos-Specialist/?_artperpage=96&pgNr={p - 1}", parse_oxid)
    if not full:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    time.sleep(PAUSE_BETWEEN_PAGES)
    extra = parse_oxid(get(session, f"{site}/Cigarren-Welt/Unsere-VERFUeGBAREN-Habanos-Cigars/?_artperpage=96"))
    merged = {i.key: i for i in full}
    for i in extra:
        if i.key not in merged or (i.available and not merged[i.key].available):
            merged[i.key] = i
    return list(merged.values())


def fetch_tabakkontor(session: requests.Session) -> list[Item]:
    # Kategorie Cuba -> Marken -> Linien; jede Seite zeigt alle Artikel ohne Blättern
    base = "https://tabak-kontor.de"
    queue = [f"{base}/onlineshop/zigarren-cigarillos/cuba"]
    seen: set[str] = set()
    items: dict[str, Item] = {}
    while queue and len(seen) < 150:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page_items, subs = parse_virtuemart(get(session, url), base)
        for i in page_items:
            items.setdefault(i.key, i)
        queue += [u for u in subs if u not in seen and "produktion-eingestellt" not in u]
        time.sleep(0.8)
    if len(items) < 50:
        raise FetchError(f"Nur {len(items)} Artikel gefunden (Seitenaufbau geändert?)")
    return list(items.values())


def fetch_habanos_zigarren(session: requests.Session) -> list[Item]:
    api = "https://www.habanos-zigarren.com/rs/shops/62530561/products?resultsPerPage=100"
    items: list[Item] = []
    total = None
    for page in range(1, 21):
        data, _ = get_json(session, f"{api}&page={page}")
        if total is None:
            total = data.get("results")
        page_items = parse_epages(data)
        if not page_items:
            break
        items.extend(page_items)
        if total and len(items) >= total:
            break
        time.sleep(PAUSE_BETWEEN_PAGES)
    items = dedupe(items)
    if not items:
        raise FetchError("Keine Produkte gefunden (Schnittstelle geändert?)")
    if total and len(items) < total:
        raise FetchError(f"Nur {len(items)} von {total} Artikeln gelesen")
    return items


def fetch_dominique(session: requests.Session) -> list[Item]:
    base = "https://www.dominiquelondon.de/shop/category/kubanische-zigarren-196"
    items, _ = collect_pages(session, lambda p: base if p == 1 else f"{base}/page/{p}", parse_odoo)
    if not items:
        raise FetchError("Keine Produkte gefunden (Seitenaufbau geändert?)")
    return items


def dedupe(items: list[Item]) -> list[Item]:
    out: dict[str, Item] = {}
    for i in items:
        out.setdefault(i.key, i)
    return list(out.values())


SHOPS = [
    Shop("zigarrentraum", "Zigarrentraum", "voll", fetch_zigarrentraum),
    Shop("casabenden", "Casa Benden", "nur_lieferbar", fetch_casabenden),
    Shop("brinkmann", "Brinkmann Finest", "nur_lieferbar", fetch_brinkmann),
    Shop("noblego", "Noblego", "voll", fetch_noblego),
    Shop("zigarrede", "zigarre.de", "voll", fetch_zigarrede),
    Shop("wolsdorff", "Wolsdorff", "voll", fetch_wolsdorff),
    # Zigarrenschachtel ist deaktiviert: Der Cloudflare-Bot-Schutz des Shops weist
    # Abrufe von GitHub-Servern ab (HTTP 403). Parser und Tests bleiben erhalten.
    # Shop("zigarrenschachtel", "Zigarrenschachtel", "nur_lieferbar",
    #      make_jtl_fetcher("https://www.zigarrenschachtel.de/kubanische-zigarren")),
    Shop("tabacstore", "Tabacstore", "nur_lieferbar", fetch_tabacstore),
    Shop("dalay", "Dalay Zigarren", "nur_lieferbar",
         make_jtl_fetcher("https://www.dalayzigarren.de/kubanische-zigarren")),
    Shop("peterheinrichs", "Peter Heinrichs", "voll", fetch_peterheinrichs),
    Shop("falkum", "Tabakhaus Falkum", "voll", fetch_falkum),
    Shop("ccigars", "c-cigars", "voll",
         make_shopify_fetcher("https://c-cigars.de", "kubanische-zigarren")),
    Shop("habanoszigarren", "habanos-zigarren.com", "voll", fetch_habanos_zigarren),
    Shop("lagalana", "La Galana", "voll",
         make_shopify_fetcher("https://lagalana.de", "habanos-zigarren")),
    # Dominique London (ehemals Casa del Puro) zeigt keinen Lagerstand: nur Neuheiten melden
    Shop("dominique", "Dominique London", "nur_neu", fetch_dominique),
    # CIGARWORLD hat dasselbe Lager wie Casa Benden (gleiche Firma): nicht doppelt melden
    Shop("starkezigarren", "StarkeZigarren", "nur_lieferbar",
         make_jtl_fetcher("https://www.starkezigarren.de/Kubanische-Zigarren-kaufen")),
    Shop("pipehouse", "Pipe House", "voll",
         make_woo_fetcher("https://pipehouse.de", "10856", brand_prefix=True, trust_total=False)),
    Shop("nobbis", "Nobbis Tabakwelt", "voll",
         make_woo_fetcher("https://www.nobbis-tabakwelt.de", "53")),
    Shop("paulbugge", "Paul Bugge", "voll", fetch_paulbugge),
    Shop("cigarrenversand24", "Cigarrenversand24", "voll", fetch_cigarrenversand24),
    Shop("tabakkontor", "Tabak-Kontor Leipzig", "voll", fetch_tabakkontor),
    Shop("herzog", "Zigarren Herzog", "voll",
         make_shopify_fetcher("https://zigarren-herzog.com", "kuba", brand_from_vendor=True)),
]


# --------------------------------------------------------------------------
# Zustand und Vergleich
# --------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"shops": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")


@dataclass
class Event:
    kind: str           # "wieder_da" | "neu" | "neu_nicht_lieferbar" | "ausverkauft"
    shop: str
    item: Item
    hours_available: float | None = None   # nur bei "ausverkauft", falls bekannt


def _hours_between(start: str | None, end: str) -> float | None:
    if not start:
        return None
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return round(delta.total_seconds() / 3600, 1)


def compare(shop: Shop, shop_state: dict, current: list[Item], timestamp: str) -> list[Event]:
    """Gleicht den aktuellen Stand mit dem gespeicherten ab und aktualisiert shop_state.

    Liefert alle Ereignisse, auch "ausverkauft" (das nur ins Verlaufsprotokoll kommt).
    """
    known: dict = shop_state.setdefault("items", {})
    first_run = not shop_state.get("initialized")
    tracks_stock = shop.mode != "nur_neu"
    events: list[Event] = []
    current_keys = set()

    for item in current:
        current_keys.add(item.key)
        old = known.get(item.key)
        if old is None:
            if not first_run:
                if not item.available:
                    kind = "neu_nicht_lieferbar"
                elif shop.mode == "nur_lieferbar":
                    # Shop zeigt nur Lieferbares: ob neu oder Nachlieferung, ist nicht erkennbar
                    kind = "neu_oder_wieder"
                else:
                    kind = "neu"
                events.append(Event(kind, shop.id, item))
            known[item.key] = {
                "name": item.name, "pack": item.pack, "price": item.price, "url": item.url,
                "group": item.group, "available": item.available, "first_seen": timestamp,
                "last_available": timestamp if item.available else None,
                # Beim Ausgangsbestand ist unbekannt, seit wann etwas verfügbar ist.
                "available_since": timestamp if (item.available and not first_run) else None,
            }
            continue
        if tracks_stock and item.available and not old.get("available"):
            events.append(Event("wieder_da", shop.id, item))
            old["available_since"] = timestamp
        elif tracks_stock and not item.available and old.get("available"):
            events.append(Event("ausverkauft", shop.id, item,
                                _hours_between(old.get("available_since"), timestamp)))
            old["available_since"] = None
        old.update({"name": item.name, "pack": item.pack, "price": item.price,
                    "url": item.url, "group": item.group, "available": item.available})
        if item.available:
            old["last_available"] = timestamp

    # Was nicht mehr gelistet ist, gilt als nicht verfügbar.
    if tracks_stock:
        for key, old in known.items():
            if key not in current_keys and old.get("available"):
                gone = Item(key=key, name=old.get("name", ""), pack=old.get("pack", ""),
                            price=old.get("price", ""), url=old.get("url", ""),
                            available=False, group=old.get("group", key))
                events.append(Event("ausverkauft", shop.id, gone,
                                    _hours_between(old.get("available_since"), timestamp)))
                old["available"] = False
                old["available_since"] = None

    shop_state["initialized"] = True
    return events


def plausible(shop: Shop, shop_state: dict, current: list[Item]) -> str | None:
    """Schutz gegen halb geladene Seiten: Gibt einen Grund zurück, wenn etwas faul ist."""
    known = shop_state.get("items", {})
    if shop.mode in ("voll", "nur_neu"):
        if not current:
            return "keine Produkte gefunden"
        listed_before = shop_state.get("last_count", 0)
        if listed_before > 50 and len(current) < listed_before * 0.5:
            return f"nur {len(current)} statt rund {listed_before} Varianten gelesen"
    else:
        available_before = sum(1 for v in known.values() if v.get("available"))
        if not current and available_before >= 5:
            return f"plötzlich 0 statt {available_before} lieferbare Artikel"
    return None


# --------------------------------------------------------------------------
# Verlaufsprotokoll
# --------------------------------------------------------------------------

def berlin_time(timestamp: str) -> datetime:
    dt = datetime.fromisoformat(timestamp)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:  # noqa: BLE001 – ohne Zeitzonendaten bleibt es bei UTC
        return dt


def log_events(events: list[Event], timestamp: str, shop_names: dict[str, str],
               path: Path | None = None) -> int:
    """Hängt Ereignisse an verlauf.csv an (Semikolon-getrennt, öffnet direkt in Excel)."""
    if not events:
        return 0
    path = path or LOG_FILE
    local = berlin_time(timestamp)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        if new_file:
            writer.writerow(LOG_COLUMNS)
        for e in events:
            hours = "" if e.hours_available is None else str(e.hours_available).replace(".", ",")
            writer.writerow([
                timestamp, local.strftime("%Y-%m-%d %H:%M"), WOCHENTAGE[local.weekday()],
                local.hour, shop_names.get(e.shop, e.shop), e.kind, e.item.name, e.item.pack,
                e.item.price, hours, e.item.url, e.item.key,
            ])
    return len(events)


# --------------------------------------------------------------------------
# Benachrichtigungen
# --------------------------------------------------------------------------

def push(title: str, message: str, click: str | None = None, tags: list[str] | None = None,
         priority: int = 4) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print(f"[kein NTFY_TOPIC gesetzt] {title}: {message}")
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": message, "priority": priority}
    if click:
        payload["click"] = click
    if tags:
        payload["tags"] = tags
    headers = {}
    token = os.environ.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(server, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Push fehlgeschlagen: {e}", file=sys.stderr)
        return False


def group_events(events: list[Event]) -> list[list[Event]]:
    """Fasst Ereignisse desselben Produkts (verschiedene Packungen) zusammen."""
    groups: dict[tuple, list[Event]] = {}
    for e in events:
        groups.setdefault((e.shop, e.kind, e.item.group), []).append(e)
    order = {"wieder_da": 0, "neu_oder_wieder": 1, "neu": 2, "neu_nicht_lieferbar": 3, "ausverkauft": 4}
    return sorted(groups.values(), key=lambda g: (order[g[0].kind], g[0].shop, g[0].item.name))


SHOP_URLS = {
    "zigarrentraum": "https://www.zigarrentraum.de/sortiment/zigarren/kubanische-zigarren/",
    "casabenden": "https://www.casabenden.de/zigarren/kuba?nur_lieferbar=1",
    "brinkmann": "https://brinkmannfinest.de/zigarren/herkunft/kuba/",
    "noblego": "https://www.noblego.de/kubanische-zigarren/",
    "zigarrede": "https://www.zigarre.de/kubanische-zigarren.html",
    "wolsdorff": "https://wolsdorff.shop/zigarren/nach-herkunft/kuba/",
    "tabacstore": "https://www.tabacstore.de/kubanische-zigarren/?immediately-available=1",
    "dalay": "https://www.dalayzigarren.de/kubanische-zigarren?availability=1",
    "peterheinrichs": "https://www.peterheinrichs.de/zigarren/kuba/",
    "falkum": "https://www.falkum.de/zigarren/habanos.html",
    "ccigars": "https://c-cigars.de/collections/kubanische-zigarren",
    "habanoszigarren": "https://www.habanos-zigarren.com/",
    "lagalana": "https://lagalana.de/collections/habanos-zigarren",
    "dominique": "https://www.dominiquelondon.de/shop/category/kubanische-zigarren-196",
    "cigarworld": "https://www.cigarworld.de/zigarren/kuba?nur_lieferbar=1",
    "starkezigarren": "https://www.starkezigarren.de/Kubanische-Zigarren-kaufen?availability=1",
    "herzog": "https://zigarren-herzog.com/collections/kuba",
    "pipehouse": "https://pipehouse.de/cigarren/kubanische-zigarren/",
    "nobbis": "https://www.nobbis-tabakwelt.de/zigarren/kubanische-zigarren/",
    "paulbugge": "https://www.paul-bugge.com/kubanische-zigarren",
    "cigarrenversand24": "https://www.cigarrenversand24.de/Cigarren-Welt/Unsere-VERFUeGBAREN-Habanos-Cigars/",
    "tabakkontor": "https://tabak-kontor.de/onlineshop/zigarren-cigarillos/cuba",
}

SECTIONS = [  # Reihenfolge, Überschrift in der Nachricht, Kurzform für den Titel
    ("wieder_da", "✅ Wieder da", "wieder da"),
    ("neu_oder_wieder", "✅ Neu oder wieder da", "neu/wieder da"),
    ("neu", "🆕 Neu", "neu"),
    ("neu_nicht_lieferbar", "👀 Neu gelistet, noch nicht lieferbar", "neu gelistet"),
]
NTFY_MAX_CHARS = 3800   # ntfy erlaubt rund 4 KB je Nachricht


def describe_group(group: list[Event]) -> str:
    """Eine Zeile je Produkt: alle Packungen mit Preis zusammengefasst."""
    item = group[0].item
    parts = []
    for e in group:
        part = " ".join(p for p in (e.item.pack, e.item.price) if p)
        if part and part not in parts:
            parts.append(part)
    return f"• {item.name}" + (f": {', '.join(parts)}" if parts else "")


def describe(group: list[Event], shop_names: dict[str, str]) -> tuple[str, str, str, list[str]]:
    """Kurzbeschreibung eines einzelnen Produkts (für Tests und --dry-run)."""
    first = group[0]
    label = next((t for k, _, t in SECTIONS if k == first.kind), first.kind)
    return (f"{shop_names[first.shop]}: {first.item.name} ({label})", describe_group(group),
            first.item.url, ["cigar"])


def shop_message(shop_id: str, events: list[Event], shop_names: dict[str, str]
                 ) -> tuple[str, str, str, list[str], int]:
    """Eine Sammelnachricht für alle Änderungen eines Shops in diesem Lauf."""
    groups = group_events([e for e in events if e.kind in ALERT_KINDS])
    by_kind: dict[str, list[list[Event]]] = {}
    for g in groups:
        by_kind.setdefault(g[0].kind, []).append(g)

    counts, blocks = [], []
    for kind, heading, short in SECTIONS:
        gs = by_kind.get(kind)
        if not gs:
            continue
        counts.append(f"{len(gs)} {short}")
        blocks.append("\n".join([heading] + [describe_group(g) for g in gs]))
    title = f"{shop_names.get(shop_id, shop_id)}: {', '.join(counts)}"

    message = "\n\n".join(blocks)
    if len(message) > NTFY_MAX_CHARS:
        cut = message[:NTFY_MAX_CHARS].rsplit("\n", 1)[0]
        hidden = message[len(cut):].count("• ")
        message = f"{cut}\n… und {hidden} weitere (siehe Shop)"

    products = {g[0].item.group for g in groups}
    url = groups[0][0].item.url if len(products) == 1 else SHOP_URLS.get(shop_id, groups[0][0].item.url)
    kinds = set(by_kind)
    if kinds & {"wieder_da", "neu_oder_wieder"}:
        tags, prio = ["white_check_mark", "cigar"], 5
    elif "neu" in kinds:
        tags, prio = ["new", "cigar"], 4
    else:
        tags, prio = ["eyes"], 3
    return title, message, url, tags, prio


def notify(events: list[Event], shop_names: dict[str, str]) -> None:
    """Eine Push-Nachricht pro Shop, in dem sich etwas getan hat."""
    alerts = [e for e in events if e.kind in ALERT_KINDS]
    for shop_id in dict.fromkeys(e.shop for e in alerts):
        title, message, url, tags, prio = shop_message(
            shop_id, [e for e in alerts if e.shop == shop_id], shop_names)
        print(f"→ {title}\n{message}\n")
        push(title, message, click=url, tags=tags, priority=prio)


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------

def run(dry_run: bool = False) -> int:
    state = load_state()
    timestamp = now_iso()
    today = timestamp[:10]
    session = make_session()
    shop_names = {s.id: s.name for s in SHOPS}
    all_events: list[Event] = []
    warnings: list[tuple[str, str]] = []

    for shop in SHOPS:
        shop_state = state["shops"].setdefault(shop.id, {"items": {}})
        print(f"== {shop.name}")
        try:
            current = [i for i in shop.fetch(session) if is_wanted(i)]
            problem = plausible(shop, shop_state, current)
            if problem:
                raise FetchError(problem)
        except Exception as e:  # noqa: BLE001 – ein Shop darf die anderen nicht blockieren
            fails = shop_state.get("failures", 0) + 1
            shop_state["failures"] = fails
            shop_state["last_error"] = f"{type(e).__name__}: {e}"[:500]
            print(f"   Fehler ({fails}. in Folge): {e}")
            if fails == FAILURE_ALERT_AFTER:
                warnings.append((shop.name, str(e)))
            continue

        available = sum(1 for i in current if i.available)
        print(f"   {len(current)} Einträge gelesen, davon {available} verfügbar")
        was_failing = shop_state.get("failures", 0) >= FAILURE_ALERT_AFTER
        first_run = not shop_state.get("initialized")
        events = compare(shop, shop_state, current, timestamp)
        shop_state["failures"] = 0
        shop_state.pop("last_error", None)
        shop_state["last_ok_day"] = today
        shop_state["last_count"] = len(current)
        if first_run:
            print("   Erster Lauf: Ausgangsbestand gespeichert, keine Alarme.")
        if was_failing:
            warnings.append((shop.name, "wieder erreichbar"))
        all_events.extend(events)

    if dry_run:
        alerts = [e for e in all_events if e.kind in ALERT_KINDS]
        for shop_id in dict.fromkeys(e.shop for e in alerts):
            title, message, _, _, _ = shop_message(
                shop_id, [e for e in alerts if e.shop == shop_id], shop_names)
            print(f"[dry-run] {title}\n{message}\n")
        sold = sum(1 for e in all_events if e.kind == "ausverkauft")
        print(f"[dry-run] {len(all_events)} Ereignisse (davon {sold} ausverkauft), "
              "nichts gespeichert oder gesendet.")
        return 0

    notify(all_events, shop_names)
    log_events(all_events, timestamp, shop_names)
    for shop_name, reason in warnings:
        if reason == "wieder erreichbar":
            push(f"{shop_name} wieder erreichbar", "Die Überwachung läuft wieder normal.",
                 tags=["ok_hand"], priority=2)
        else:
            push(f"Problem mit {shop_name}",
                 f"Der Shop konnte {FAILURE_ALERT_AFTER}× in Folge nicht gelesen werden:\n{reason}",
                 tags=["warning"], priority=3)
    save_state(state)
    alerts = sum(1 for e in all_events if e.kind in ALERT_KINDS)
    print(f"Fertig: {alerts} Alarme, {len(all_events)} Ereignisse im Verlauf.")
    return 0


def show_status() -> None:
    state = load_state()
    names = {s.id: s.name for s in SHOPS}
    for shop_id, shop_state in state.get("shops", {}).items():
        avail = [v for v in shop_state.get("items", {}).values() if v.get("available")]
        print(f"\n{names.get(shop_id, shop_id)}: {len(avail)} verfügbar")
        for v in sorted(avail, key=lambda v: v["name"]):
            print(f"  • {v['name']} {v.get('pack', '')} {v.get('price', '')}".rstrip())


def send_test_push() -> bool:
    """Beispiel-Sammelnachricht im echten Format (Inhalt ist erfunden)."""
    def ex(key, name, pack, price, group=None):
        return Item(key=key, name=name, pack=pack, price=price,
                    url=SHOP_URLS["noblego"], available=True, group=group or key)
    events = [
        Event("wieder_da", "noblego", ex("1", "Cohiba Siglo VI", "10er", "290,00 €", "a")),
        Event("wieder_da", "noblego", ex("2", "Cohiba Siglo VI", "Einzeln", "29,00 €", "a")),
        Event("wieder_da", "noblego", ex("3", "Partagás Serie D No.4", "25er", "540,00 €")),
        Event("neu", "noblego", ex("4", "Trinidad Edición Limitada", "12er", "780,00 €")),
    ]
    title, message, url, tags, _ = shop_message("noblego", events, {"noblego": "Noblego"})
    return push(f"TEST · {title}", message + "\n\n(Beispiel, keine echten Bestände)",
                click=url, tags=tags, priority=3)


def main() -> int:
    ap = argparse.ArgumentParser(description="Habanos-Bestandswächter")
    ap.add_argument("--dry-run", action="store_true", help="nichts speichern oder senden")
    ap.add_argument("--test-push", action="store_true", help="Test-Benachrichtigung senden")
    ap.add_argument("--status", action="store_true", help="verfügbare Artikel anzeigen")
    args = ap.parse_args()
    if args.test_push:
        return 0 if send_test_push() else 1
    if args.status:
        show_status()
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
