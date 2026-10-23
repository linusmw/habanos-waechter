#!/usr/bin/env python3
"""
Neuheiten-Verzeichnis: Wann haben die Shops welche Zigarre erstmals online gestellt?

Manche Shopsysteme speichern zu jedem Produkt den Zeitpunkt der Anlage bzw.
Veröffentlichung und geben ihn öffentlich heraus:

  Shopify      (c-cigars, La Galana, Zigarren Herzog)   created_at, published_at
  WooCommerce  (Zigarrentraum, Pipe House, Nobbis)      date_gmt der Produktseite

Daraus entsteht neuheiten.csv mit einer Zeile je Produkt:

  art = "neu"            Zeitpunkt, zu dem das Produkt angelegt wurde
  art = "wieder online"  nur Shopify: das Produkt wurde später erneut veröffentlicht
                         (published_at deutlich nach created_at; oft nach Ausverkauf)

Tage, an denen ein Shop sehr viele Produkte auf einmal angelegt hat, sind Umzüge oder
Importe des Sortiments und keine echten Neuheiten; sie werden weggelassen.

Aufruf:  python neuheiten.py            schreibt neuheiten.csv
         python neuheiten.py --dry-run  zeigt nur eine Zusammenfassung
"""

from __future__ import annotations

import argparse
import csv
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import waechter as w

OUT_FILE = Path(__file__).with_name("neuheiten.csv")
COLUMNS = ["zeit_utc", "zeit_berlin", "wochentag", "stunde", "shop", "artikel", "art"]
BERLIN = ZoneInfo("Europe/Berlin")
IMPORT_SCHWELLE = 15            # so viele Anlagen an einem Tag = Import, keine Neuheit
WIEDER_ONLINE_AB = timedelta(days=2)

SHOPIFY = [  # (Shopname, Basis, Kollektion, Marke aus 'vendor' voranstellen)
    ("c-cigars", "https://c-cigars.de", "kubanische-zigarren", False),
    ("La Galana", "https://lagalana.de", "habanos-zigarren", False),
    ("Zigarren Herzog", "https://zigarren-herzog.com", "kuba", True),
]
WOO = [  # (Shopname, Basis, Kategorie-Slug, Marke aus der Store API voranstellen)
    ("Zigarrentraum", "https://www.zigarrentraum.de", "kubanische-zigarren", False),
    ("Pipe House", "https://pipehouse.de", "kubanische-zigarren", True),
    ("Nobbis Tabakwelt", "https://www.nobbis-tabakwelt.de", "kubanische-zigarren", False),
]


def parse_time(value: str) -> datetime | None:
    """ISO-Zeit mit oder ohne Zeitzone; ohne gilt UTC (WooCommerce date_gmt)."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def row(t: datetime, shop: str, artikel: str, art: str) -> dict:
    b = t.astimezone(BERLIN)
    return {"zeit_utc": t.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "zeit_berlin": b.strftime("%Y-%m-%d %H:%M"), "wochentag": w.WOCHENTAGE[b.weekday()],
            "stunde": b.hour, "shop": shop, "artikel": artikel, "art": art}


def wanted(name: str) -> bool:
    return w.is_wanted(w.Item(key="", name=name, pack="", price="", url="", available=True, group=""))


def shopify_rows(shop: str, products: list[dict], brand_from_vendor: bool) -> list[dict]:
    out = []
    for p in products:
        name = w.text(p.get("title"))
        vendor = w.text(p.get("vendor"))
        if brand_from_vendor and vendor and vendor.lower() not in name.lower():
            name = f"{vendor} {name}"
        if not name or not wanted(name):
            continue
        created, published = parse_time(p.get("created_at", "")), parse_time(p.get("published_at") or "")
        if created:
            out.append(row(created, shop, name, "neu"))
        if created and published and published - created > WIEDER_ONLINE_AB:
            out.append(row(published, shop, name, "wieder online"))
    return out


def woo_rows(shop: str, posts: list[dict], brands: dict[int, str]) -> list[dict]:
    out = []
    for p in posts:
        name = w.text((p.get("title") or {}).get("rendered"))
        brand = brands.get(p.get("id"), "")
        if brand and brand.lower() not in name.lower():
            name = f"{brand} {name}"
        t = parse_time(p.get("date_gmt", ""))
        if name and t and wanted(name):
            out.append(row(t, shop, name, "neu"))
    return out


def drop_imports(rows: list[dict]) -> tuple[list[dict], list[tuple[str, str, int]]]:
    """Lässt Tage weg, an denen ein Shop sehr viele Produkte auf einmal angelegt hat."""
    per_day = Counter((r["shop"], r["zeit_berlin"][:10]) for r in rows if r["art"] == "neu")
    imports = {k for k, n in per_day.items() if n >= IMPORT_SCHWELLE}
    kept = [r for r in rows if not (r["art"] == "neu" and (r["shop"], r["zeit_berlin"][:10]) in imports)]
    return kept, sorted((s, d, per_day[(s, d)]) for s, d in imports)


def fetch_shopify(session, base: str, collection: str) -> list[dict]:
    products: dict[int, dict] = {}
    for page in range(1, 21):
        data, _ = w.get_json(session, f"{base}/collections/{collection}/products.json?limit=250&page={page}")
        if not data.get("products"):
            break
        for p in data["products"]:
            products[p["id"]] = p
        time.sleep(w.PAUSE_BETWEEN_PAGES)
    return list(products.values())


def fetch_woo(session, base: str, slug: str, with_brands: bool) -> tuple[list[dict], dict[int, str]]:
    cats, _ = w.get_json(session, f"{base}/wp-json/wp/v2/product_cat?slug={slug}&_fields=id")
    if not cats:
        raise w.FetchError(f"Kategorie {slug} nicht gefunden")
    cat = cats[0]["id"]
    posts: dict[int, dict] = {}
    for page in range(1, 31):
        data, headers = w.get_json(session, f"{base}/wp-json/wp/v2/product?product_cat={cat}&per_page=100"
                                            f"&page={page}&_fields=id,date_gmt,title")
        new = [p for p in data if p["id"] not in posts]
        for p in data:
            posts[p["id"]] = p
        if not new or len(data) < 100 or page >= int(headers.get("X-WP-TotalPages", 1) or 1):
            break
        time.sleep(w.PAUSE_BETWEEN_PAGES)
    brands: dict[int, str] = {}
    if with_brands:
        for page in range(1, 31):
            data, _ = w.get_json(session, f"{base}/wp-json/wc/store/v1/products?category={cat}"
                                          f"&per_page=100&page={page}")
            if not data:
                break
            for p in data:
                b = [w.text(x.get("name")) for x in p.get("brands") or [] if isinstance(x, dict)]
                if b and b[0]:
                    brands[p["id"]] = b[0]
            if len(data) < 100:
                break
            time.sleep(w.PAUSE_BETWEEN_PAGES)
    return list(posts.values()), brands


def collect() -> tuple[list[dict], list[str]]:
    session = w.make_session()
    rows: list[dict] = []
    problems: list[str] = []
    for shop, base, coll, vendor in SHOPIFY:
        try:
            rows += shopify_rows(shop, fetch_shopify(session, base, coll), vendor)
        except Exception as e:  # noqa: BLE001 – ein Shop darf die anderen nicht blockieren
            problems.append(f"{shop}: {e}")
    for shop, base, slug, brands in WOO:
        try:
            posts, brand_map = fetch_woo(session, base, slug, brands)
            rows += woo_rows(shop, posts, brand_map)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{shop}: {e}")
    return rows, problems


def merge_previous(rows: list[dict], shops_ok: set[str], path: Path = OUT_FILE) -> list[dict]:
    """Behält Einträge früherer Läufe: Produkte, die ein Shop inzwischen gelöscht hat,
    bleiben so im Verzeichnis; Shops, die diesmal nicht lesbar waren, ebenso."""
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", newline="") as f:
        old = list(csv.DictReader(f, delimiter=";"))
    have = {(r["shop"], r["artikel"], r["art"], r["zeit_utc"]) for r in rows}
    for r in old:
        key = (r["shop"], r["artikel"], r["art"], r["zeit_utc"])
        if key in have:
            continue
        if r["art"] == "wieder online" and r["shop"] in shops_ok:
            continue  # ein neueres published_at ersetzt das alte
        rows.append(r)
    return rows


def save(rows: list[dict], path: Path = OUT_FILE) -> None:
    rows = sorted(rows, key=lambda r: (r["zeit_utc"], r["shop"], r["artikel"]))
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        out.writeheader()
        out.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Neuheiten-Verzeichnis der Shops erstellen")
    ap.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts speichern")
    args = ap.parse_args()
    rows, problems = collect()
    rows, imports = drop_imports(rows)
    for s, d, n in imports:
        print(f"Import übersprungen: {s} am {d} ({n} Produkte)")
    for p in problems:
        print(f"Nicht lesbar: {p}")
    shops_ok = {r["shop"] for r in rows}
    print("Einträge:", dict(Counter((r["shop"], r["art"]) for r in rows)))
    if not args.dry_run:
        save(merge_previous(rows, shops_ok))
        print(f"Gespeichert: {OUT_FILE.name}")


if __name__ == "__main__":
    main()
