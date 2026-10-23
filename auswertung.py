#!/usr/bin/env python3
"""
Auswertung des Verlaufs (verlauf.csv): Wann füllen die Shops nach, wie lange hält die Ware?

Zusätzlich fließen historische Fundmeldungen aus historie.csv in die Zeitmuster ein
(Spalten: zeit_utc; zeit_berlin; wochentag; stunde; shop; artikel). Sie zählen nur bei
Wochentag/Tageszeit und den meistgefundenen Zigarren, nicht bei Wareneingängen oder
Haltbarkeit, weil dafür der genaue Lagerstand fehlt.

Aufruf:
  python auswertung.py                 Bericht auf dem Bildschirm
  python auswertung.py -o auswertung.md  Bericht als Markdown-Datei

Begriffe:
  Wareneingang  = ein Lauf, in dem bei einem Shop mindestens WELLE_MIN Artikel
                  gleichzeitig wieder da oder neu waren (typisch für eine Lieferung)
  Haltbarkeit   = Stunden zwischen "wieder da"/"neu" und "ausverkauft"
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).with_name("verlauf.csv")
HIST_FILE = Path(__file__).with_name("historie.csv")
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
TAGESZEITEN = [("Nacht", 0, 6), ("Morgen", 6, 10), ("Vormittag", 10, 12),
               ("Mittag", 12, 14), ("Nachmittag", 14, 18), ("Abend", 18, 24)]
ZUGANG = {"wieder_da", "neu", "neu_oder_wieder"}
WELLE_MIN = 3          # ab so vielen gleichzeitigen Zugängen gilt es als Wareneingang
MIN_DATEN = 5          # darunter keine Muster-Aussagen


def lade_historie(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for r in rows:
        r["stunde"] = int(r.get("stunde") or 0)
        r["ereignis"] = "historisch"
    return rows


def artikel_liste(r: dict) -> list[str]:
    return [a.strip() for a in (r.get("artikel") or "").split(" + ")
            if a.strip() and a.strip() != "(Sortiment)"]


def lade(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for r in rows:
        r["stunde"] = int(r.get("stunde") or 0)
        h = (r.get("verfuegbar_stunden") or "").replace(",", ".")
        r["dauer"] = float(h) if h else None
    return rows


def tageszeit(stunde: int) -> str:
    for name, von, bis in TAGESZEITEN:
        if von <= stunde < bis:
            return name
    return "?"


def fmt_dauer(stunden: float) -> str:
    if stunden < 1:
        return "unter 1 Std."
    if stunden < 48:
        return f"{stunden:.0f} Std."
    return f"{stunden / 24:.1f} Tage".replace(".", ",")


def balken(counter: Counter, keys: list[str]) -> str:
    """Kleine Textgrafik, z. B. Mo ▁ Di ▅ Mi █ ..."""
    stufen = " ▁▂▃▄▅▆▇█"
    top = max((counter.get(k, 0) for k in keys), default=0) or 1
    return "  ".join(f"{k} {stufen[round(counter.get(k, 0) / top * 8)]}{counter.get(k, 0)}"
                     for k in keys)


def wareneingaenge(rows: list[dict]) -> dict[str, list[dict]]:
    """Gruppiert Zugänge je Shop und Lauf; große Gruppen sind Wareneingänge."""
    laeufe: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["ereignis"] in ZUGANG:
            laeufe[(r["shop"], r["zeit_utc"])].append(r)
    out: dict[str, list[dict]] = defaultdict(list)
    for (shop, zeit), gruppe in sorted(laeufe.items(), key=lambda kv: kv[0][1]):
        if len(gruppe) >= WELLE_MIN:
            out[shop].append({"zeit": gruppe[0]["zeit_berlin"], "wochentag": gruppe[0]["wochentag"],
                              "stunde": gruppe[0]["stunde"], "anzahl": len(gruppe),
                              "utc": zeit,
                              "artikel": sorted({g["artikel"] for g in gruppe})})
    return out


def abstaende_tage(wellen: list[dict]) -> list[float]:
    zeiten = [datetime.fromisoformat(w["utc"]) for w in wellen]
    return [(b - a).total_seconds() / 86400 for a, b in zip(zeiten, zeiten[1:])]


def naechster_eingang(wellen: list[dict]) -> str | None:
    """Schätzt den nächsten Wareneingang, aber nur bei erkennbar regelmäßigem Rhythmus:
    mindestens 3 Abstände, die höchstens 30 % um ihren Mittelwert schwanken."""
    abst = abstaende_tage(wellen)
    if len(abst) < 3:
        return None
    mittel = statistics.mean(abst)
    if mittel <= 0 or statistics.pstdev(abst) > 0.3 * mittel:
        return None
    from datetime import timedelta
    letzter = datetime.strptime(wellen[-1]["zeit"], "%Y-%m-%d %H:%M")
    naechst = letzter + timedelta(days=mittel)
    stunden = Counter(w["stunde"] for w in wellen).most_common(1)[0][0]
    return f"{WOCHENTAGE[naechst.weekday()]} {naechst:%d.%m.}, gegen {stunden} Uhr"


def bericht(rows: list[dict], historie: list[dict] | None = None) -> str:
    z: list[str] = ["# Auswertung Habanos-Wächter", ""]
    historie = historie or []
    if not rows and not historie:
        z += ["Noch keine Daten. Der Verlauf füllt sich, sobald sich in einem Shop etwas ändert."]
        return "\n".join(z) + "\n"
    if not rows:
        z += ["Der eigene Verlauf ist noch leer; unten stehen die historischen Funde.", ""]
        return "\n".join(z + historie_abschnitt(historie)) + "\n"

    zugang = [r for r in rows if r["ereignis"] in ZUGANG]
    weg = [r for r in rows if r["ereignis"] == "ausverkauft"]
    erster, letzter = rows[0]["zeit_berlin"], rows[-1]["zeit_berlin"]
    z += [f"Zeitraum: {erster} bis {letzter} (deutsche Zeit) · "
          f"{len(zugang)} Zugänge · {len(weg)}× ausverkauft", ""]
    if len(zugang) < MIN_DATEN:
        z += ["> Noch zu wenige Daten für belastbare Muster. Aussagekräftig wird es nach "
              "einigen Wochen, idealerweise nach 2–3 Monaten.", ""]

    # ---------------------------------------------------------------- Überblick
    z += ["## Überblick je Shop", "",
          "| Shop | wieder da | neu | ausverkauft | Wareneingänge | Haltbarkeit (Median) |",
          "|---|---:|---:|---:|---:|---|"]
    wellen = wareneingaenge(rows)
    shops = sorted({r["shop"] for r in rows})
    for shop in sorted(shops, key=lambda s: -sum(1 for r in zugang if r["shop"] == s)):
        r_shop = [r for r in rows if r["shop"] == shop]
        dauern = [r["dauer"] for r in r_shop if r["ereignis"] == "ausverkauft" and r["dauer"] is not None]
        med = fmt_dauer(statistics.median(dauern)) if dauern else "–"
        z.append(f"| {shop} | {sum(r['ereignis'] == 'wieder_da' for r in r_shop)} "
                 f"| {sum(r['ereignis'] == 'neu' for r in r_shop)} "
                 f"| {sum(r['ereignis'] == 'ausverkauft' for r in r_shop)} "
                 f"| {len(wellen.get(shop, []))} | {med} |")
    z.append("")

    # ---------------------------------------------------------------- Wann wird nachgefüllt?
    muster = zugang + historie
    z += ["## Wann füllen die Shops nach?", "",
          "Zugänge (wieder da + neu) nach Wochentag und Tageszeit"
          + (f", inklusive {len(historie)} historischer Funde." if historie else "."), ""]
    z += [f"**Alle Shops:** {balken(Counter(r['wochentag'] for r in muster), WOCHENTAGE)}", "",
          f"{balken(Counter(tageszeit(r['stunde']) for r in muster), [t[0] for t in TAGESZEITEN])}", ""]
    for shop in sorted({r["shop"] for r in muster}, key=lambda s: -sum(r["shop"] == s for r in muster)):
        r_zu = [r for r in muster if r["shop"] == shop]
        if len(r_zu) < MIN_DATEN:
            continue
        tage = Counter(r["wochentag"] for r in r_zu)
        zeit = Counter(tageszeit(r["stunde"]) for r in r_zu)
        top_tag, n_tag = tage.most_common(1)[0]
        top_zeit, n_zeit = zeit.most_common(1)[0]
        n_hist = sum(r["ereignis"] == "historisch" for r in r_zu)
        quelle = f" ({len(r_zu)} Funde, davon {n_hist} historisch)" if n_hist else f" ({len(r_zu)} Funde)"
        z += [f"### {shop}{quelle}",
              f"- Wochentage: {balken(tage, WOCHENTAGE)}",
              f"- Tageszeit: {balken(zeit, [t[0] for t in TAGESZEITEN])}",
              f"- Am häufigsten: **{top_tag}** ({n_tag / len(r_zu):.0%}), "
              f"**{top_zeit}** ({n_zeit / len(r_zu):.0%})"]
        w = wellen.get(shop, [])
        if len(w) >= 2:
            abst = abstaende_tage(w)
            z.append(f"- Wareneingänge im Schnitt alle **{statistics.mean(abst):.1f} Tage**".replace(".", ",")
                     + f" (zuletzt {w[-1]['zeit']})")
            prognose = naechster_eingang(w)
            if prognose:
                z.append(f"- Prognose nächster Wareneingang: **{prognose}**")
        z.append("")

    # ---------------------------------------------------------------- Wareneingänge
    if wellen:
        z += ["## Letzte Wareneingänge", "",
              f"Läufe, in denen bei einem Shop mindestens {WELLE_MIN} Artikel gleichzeitig kamen.", "",
              "| Zeit | Shop | Artikel | Beispiele |", "|---|---|---:|---|"]
        alle = sorted(((w, s) for s, ws in wellen.items() for w in ws), key=lambda x: x[0]["utc"])
        for w, shop in reversed(alle[-15:]):
            beispiele = ", ".join(w["artikel"][:3]) + (" …" if len(w["artikel"]) > 3 else "")
            z.append(f"| {w['wochentag']} {w['zeit']} | {shop} | {w['anzahl']} | {beispiele} |")
        z.append("")

    # ---------------------------------------------------------------- Wie lange hält die Ware?
    dauern = [r for r in weg if r["dauer"] is not None]
    if dauern:
        z += ["## Wie schnell ist die Ware wieder weg?", ""]
        je_artikel: dict[tuple, list[float]] = defaultdict(list)
        for r in dauern:
            je_artikel[(r["artikel"], r["packung"], r["shop"])].append(r["dauer"])
        kurz = sorted(je_artikel.items(), key=lambda kv: statistics.median(kv[1]))[:12]
        z += ["Am schnellsten vergriffen (typische Zeit von Zugang bis ausverkauft):", "",
              "| Artikel | Shop | weg nach (Median) | beobachtet |", "|---|---|---|---:|"]
        for (artikel, packung, shop), werte in kurz:
            name = artikel + (f" ({packung})" if packung else "")
            z.append(f"| {name} | {shop} | {fmt_dauer(statistics.median(werte))} | {len(werte)}× |")
        z.append("")

    # ---------------------------------------------------------------- Häufig nachgefüllt
    haeufig = Counter((r["artikel"], r["shop"]) for r in zugang if r["ereignis"] == "wieder_da")
    if haeufig:
        z += ["## Am häufigsten wieder da", "", "| Artikel | Shop | Mal |", "|---|---|---:|"]
        for (artikel, shop), n in haeufig.most_common(15):
            z.append(f"| {artikel} | {shop} | {n} |")
        z.append("")

    if historie:
        z += historie_abschnitt(historie)

    z += ["---", "*Erzeugt aus `verlauf.csv` und `historie.csv`. Zeitpunkte im eigenen Verlauf "
          "sind auf etwa 30 Minuten genau (Prüfintervall des Wächters), historische Funde "
          "geben den Zeitpunkt der Meldung an.*"]
    return "\n".join(z) + "\n"


def historie_abschnitt(historie: list[dict]) -> list[str]:
    z = ["## Historische Funde", "",
         f"{len(historie)} Fundmeldungen von {historie[0]['zeit_berlin'][:10]} bis "
         f"{historie[-1]['zeit_berlin'][:10]}.", "",
         "| Shop | Funde | seit 2026 | zuletzt | typisch |", "|---|---:|---:|---|---|"]
    je_shop: dict[str, list[dict]] = defaultdict(list)
    for r in historie:
        je_shop[r["shop"]].append(r)
    for shop, rs in sorted(je_shop.items(), key=lambda kv: -len(kv[1])):
        typisch = "–"
        if len(rs) >= MIN_DATEN:
            tag = Counter(r["wochentag"] for r in rs).most_common(1)[0][0]
            zeit = Counter(tageszeit(r["stunde"]) for r in rs).most_common(1)[0][0]
            typisch = f"{tag}, {zeit}"
        z.append(f"| {shop} | {len(rs)} | {sum(r['zeit_utc'] >= '2026' for r in rs)} "
                 f"| {rs[-1]['zeit_berlin'][:10]} | {typisch} |")
    z.append("")
    gefunden = Counter(a for r in historie for a in artikel_liste(r))
    if gefunden:
        z += ["Am häufigsten gefunden:", "", "| Zigarre | Funde | Shops |", "|---|---:|---|"]
        for artikel, n in gefunden.most_common(15):
            shops = sorted({r["shop"] for r in historie if artikel in artikel_liste(r)})
            z.append(f"| {artikel} | {n} | {', '.join(shops)} |")
        z.append("")
    return z


def main() -> None:
    ap = argparse.ArgumentParser(description="Muster im Habanos-Verlauf auswerten")
    ap.add_argument("-i", "--input", type=Path, default=LOG_FILE, help="Verlaufsdatei")
    ap.add_argument("-o", "--output", type=Path, help="Bericht als Markdown-Datei speichern")
    args = ap.parse_args()
    text = bericht(lade(args.input), lade_historie(HIST_FILE))
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Bericht gespeichert: {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
