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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
PAUSE_BETWEEN_PAGES = 1.5   # Sekunden; schont die Shops
MAX_PUSHES_PER_RUN = 8      # mehr Ereignisse werden zu einer Sammelnachricht
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

def pack_label(value: str) -> str:
    value = clean(unescape(value))
    return f"{value}er" if value.isdigit() else value


def money(amount: str | int | None, minor_unit: int = 2) -> str:
    if amount in (None, ""):
        return ""
    return euro(str(int(amount) / 10 ** minor_unit))


def parse_zigarrentraum_api(products: list[dict], variations: list[dict]) -> list[Item]:
    """Daten aus der WooCommerce-Schnittstelle von zigarrentraum.de.

    ``products``: alle Produkte der Kategorie, ``variations``: die Packungsvarianten
    aller Produkte, die als vorrätig gemeldet sind. Ist ein Produkt nicht vorrätig,
    ist es auch keine seiner Packungen; dafür braucht es keinen Abruf.
    Ein Eintrag je Packungsvariante, Schlüssel = Varianten-ID des Shops.
    """
    by_id = {v["id"]: v for v in variations}
    items: list[Item] = []
    for p in products:
        name = clean(unescape(p.get("name", "")))
        url = p.get("permalink", "")
        minor = (p.get("prices") or {}).get("currency_minor_unit", 2)
        group = str(p["id"])
        if p.get("variations"):
            for v in p["variations"]:
                attrs = v.get("attributes") or []
                pack = pack_label(attrs[0]["value"]) if attrs else ""
                detail = by_id.get(v["id"])
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
    """Liste 'nur sofort lieferbare Artikel' von casabenden.de.

    Gibt die Artikel und die vom Shop gemeldete Gesamtzahl zurück.
    """
    soup = BeautifulSoup(html, "html.parser")
    m = re.search(r"(\d+)\s+Artikel gefunden", soup.get_text(" "))
    total = int(m.group(1)) if m else None
    items: list[Item] = []
    for a in soup.select("a.search-result-item-inner"):
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


# --------------------------------------------------------------------------
# Shops
# --------------------------------------------------------------------------

@dataclass
class Shop:
    id: str
    name: str
    # "voll": Liste enthält auch nicht lieferbare Artikel
    # "nur_lieferbar": was fehlt, ist nicht lieferbar
    mode: str
    fetch: Callable[[requests.Session], list[Item]]


def get_json(session: requests.Session, url: str) -> tuple[list, requests.structures.CaseInsensitiveDict]:
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
    api = "https://www.zigarrentraum.de/wp-json/wc/store/v1/products"
    products: dict[int, dict] = {}
    total = None
    for page in range(1, 31):
        data, headers = get_json(session, f"{api}?category=kubanische-zigarren&per_page=100"
                                          f"&page={page}&orderby=id&order=asc")
        if total is None:
            total = int(headers.get("X-WP-Total", 0))
        if not data:
            break
        for p in data:
            products[p["id"]] = p
        if len(products) >= total:
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
    return parse_zigarrentraum_api(list(products.values()), variations)


def fetch_casabenden(session: requests.Session) -> list[Item]:
    base = "https://www.casabenden.de/zigarren/kuba?nur_lieferbar=1&shoporder=Relevanz&von={}"
    items: list[Item] = []
    total = None
    offset = 0
    for _ in range(20):
        html = get(session, base.format(offset))
        page_items, page_total = parse_casabenden_lieferbar(html)
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


def dedupe(items: list[Item]) -> list[Item]:
    out: dict[str, Item] = {}
    for i in items:
        out.setdefault(i.key, i)
    return list(out.values())


SHOPS = [
    Shop("zigarrentraum", "Zigarrentraum", "voll", fetch_zigarrentraum),
    Shop("casabenden", "Casa Benden", "nur_lieferbar", fetch_casabenden),
    Shop("brinkmann", "Brinkmann Finest", "nur_lieferbar", fetch_brinkmann),
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
    kind: str           # "wieder_da" | "neu" | "neu_nicht_lieferbar"
    shop: str
    item: Item


def compare(shop: Shop, shop_state: dict, current: list[Item], timestamp: str) -> list[Event]:
    """Gleicht den aktuellen Stand mit dem gespeicherten ab und aktualisiert shop_state."""
    known: dict = shop_state.setdefault("items", {})
    first_run = not shop_state.get("initialized")
    events: list[Event] = []
    current_keys = set()

    for item in current:
        current_keys.add(item.key)
        old = known.get(item.key)
        if old is None:
            if not first_run:
                kind = "neu" if item.available else "neu_nicht_lieferbar"
                events.append(Event(kind, shop.id, item))
            known[item.key] = {
                "name": item.name, "pack": item.pack, "price": item.price, "url": item.url,
                "group": item.group, "available": item.available, "first_seen": timestamp,
                "last_available": timestamp if item.available else None,
            }
            continue
        if item.available and not old.get("available"):
            events.append(Event("wieder_da", shop.id, item))
        old.update({"name": item.name, "pack": item.pack, "price": item.price,
                    "url": item.url, "group": item.group, "available": item.available})
        if item.available:
            old["last_available"] = timestamp

    # Was nicht mehr gelistet ist, gilt als nicht verfügbar.
    for key, old in known.items():
        if key not in current_keys and old.get("available"):
            old["available"] = False

    shop_state["initialized"] = True
    return events


def plausible(shop: Shop, shop_state: dict, current: list[Item]) -> str | None:
    """Schutz gegen halb geladene Seiten: Gibt einen Grund zurück, wenn etwas faul ist."""
    known = shop_state.get("items", {})
    if shop.mode == "voll":
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
    order = {"wieder_da": 0, "neu": 1, "neu_nicht_lieferbar": 2}
    return sorted(groups.values(), key=lambda g: (order[g[0].kind], g[0].shop, g[0].item.name))


def describe(group: list[Event], shop_names: dict[str, str]) -> tuple[str, str, str, list[str]]:
    first = group[0]
    shop = shop_names[first.shop]
    item = first.item
    if first.kind == "wieder_da":
        title, tags = f"Wieder da bei {shop}: {item.name}", ["white_check_mark", "cigar"]
    elif first.kind == "neu":
        title, tags = f"Neu bei {shop}: {item.name}", ["new", "cigar"]
    else:
        title, tags = f"Neu gelistet bei {shop}: {item.name}", ["eyes"]
    lines = []
    for e in group:
        parts = [p for p in (e.item.pack, e.item.price) if p]
        lines.append("• " + " – ".join(parts) if parts else "• " + e.item.name)
    if first.kind == "neu_nicht_lieferbar":
        lines.append("(noch nicht lieferbar)")
    return title, "\n".join(lines), item.url, tags


def notify(events: list[Event], shop_names: dict[str, str]) -> None:
    groups = group_events(events)
    for group in groups[:MAX_PUSHES_PER_RUN]:
        title, message, url, tags = describe(group, shop_names)
        prio = 5 if group[0].kind == "wieder_da" else 4 if group[0].kind == "neu" else 3
        print(f"→ {title}\n{message}\n")
        push(title, message, click=url, tags=tags, priority=prio)
    rest = groups[MAX_PUSHES_PER_RUN:]
    if rest:
        lines = [f"• {shop_names[g[0].shop]}: {g[0].item.name}" for g in rest[:25]]
        if len(rest) > 25:
            lines.append(f"… und {len(rest) - 25} weitere")
        push(f"{len(rest)} weitere Änderungen", "\n".join(lines), tags=["cigar"], priority=3)
        print(f"→ Sammelnachricht über {len(rest)} weitere Änderungen")


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
            current = shop.fetch(session)
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
        for group in group_events(all_events):
            title, message, _, _ = describe(group, shop_names)
            print(f"[dry-run] {title}\n{message}\n")
        print(f"[dry-run] {len(all_events)} Ereignisse, nichts gespeichert oder gesendet.")
        return 0

    notify(all_events, shop_names)
    for shop_name, reason in warnings:
        if reason == "wieder erreichbar":
            push(f"{shop_name} wieder erreichbar", "Die Überwachung läuft wieder normal.",
                 tags=["ok_hand"], priority=2)
        else:
            push(f"Problem mit {shop_name}",
                 f"Der Shop konnte {FAILURE_ALERT_AFTER}× in Folge nicht gelesen werden:\n{reason}",
                 tags=["warning"], priority=3)
    save_state(state)
    print(f"Fertig: {len(all_events)} Ereignisse.")
    return 0


def show_status() -> None:
    state = load_state()
    names = {s.id: s.name for s in SHOPS}
    for shop_id, shop_state in state.get("shops", {}).items():
        avail = [v for v in shop_state.get("items", {}).values() if v.get("available")]
        print(f"\n{names.get(shop_id, shop_id)}: {len(avail)} verfügbar")
        for v in sorted(avail, key=lambda v: v["name"]):
            print(f"  • {v['name']} {v.get('pack', '')} {v.get('price', '')}".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser(description="Habanos-Bestandswächter")
    ap.add_argument("--dry-run", action="store_true", help="nichts speichern oder senden")
    ap.add_argument("--test-push", action="store_true", help="Test-Benachrichtigung senden")
    ap.add_argument("--status", action="store_true", help="verfügbare Artikel anzeigen")
    args = ap.parse_args()
    if args.test_push:
        ok = push("Habanos-Wächter: Test", "Wenn du das liest, kommen die Alarme an. 🎉",
                  tags=["cigar"], priority=3)
        return 0 if ok else 1
    if args.status:
        show_status()
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
