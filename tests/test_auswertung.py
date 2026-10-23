from datetime import datetime, timedelta, timezone

import auswertung as a
import waechter as w


def test_report_detects_weekly_restock(tmp_path):
    path = tmp_path / "verlauf.csv"
    names = {"noblego": "Noblego"}
    start = datetime(2026, 8, 4, 7, 26, tzinfo=timezone.utc)          # Dienstag, 9:26 Uhr
    for week in range(5):
        t = start + timedelta(days=7 * week)
        ev = [w.Event("wieder_da", "noblego", w.Item(k, k, "25er", "", "u", True)) for k in "abcd"]
        w.log_events(ev, t.isoformat(), names, path)
        gone = [w.Event("ausverkauft", "noblego", e.item, 24.0) for e in ev]
        w.log_events(gone, (t + timedelta(days=1)).isoformat(), names, path)
    text = a.bericht(a.lade(path))
    assert "| Noblego | 20 | 0 | 20 | 5 | 24 Std. |" in text
    assert "Am häufigsten: **Di** (100%)" in text
    assert "alle **7,0 Tage**" in text
    assert "Prognose nächster Wareneingang: **Di 08.09., gegen 9 Uhr**" in text


def test_no_forecast_without_rhythm(tmp_path):
    wellen = [{"utc": t, "zeit": t[:16].replace("T", " "), "stunde": 9} for t in
              ["2026-08-04T09:26:00+00:00", "2026-08-05T09:26:00+00:00",
               "2026-08-20T09:26:00+00:00", "2026-08-22T09:26:00+00:00"]]
    assert a.naechster_eingang(wellen) is None


def test_empty_report(tmp_path):
    assert "Noch keine Daten" in a.bericht(a.lade(tmp_path / "fehlt.csv"))


def test_historie_fliesst_in_muster(tmp_path):
    hist = tmp_path / "historie.csv"
    hist.write_text("zeit_utc;zeit_berlin;wochentag;stunde;shop;artikel\n" + "".join(
        f"2026-0{m}-0{d}T12:00:00+00:00;2026-0{m}-0{d} 14:00;Do;14;Brinkmann Finest;"
        f"{'Partagas Serie D No. 4 + Montecristo No. 2' if d == 1 else '(Sortiment)'}\n"
        for m in (3, 4, 5) for d in (1, 2)), encoding="utf-8")
    text = a.bericht([], a.lade_historie(hist))
    assert "## Historische Funde" in text
    assert "| Brinkmann Finest | 6 | 6 | 2026-05-02 | Do, Nachmittag |" in text
    assert "| Partagas Serie D No. 4 | 3 | Brinkmann Finest |" in text
    assert "(Sortiment)" not in text.split("Am häufigsten gefunden")[1]


def test_historie_und_verlauf_gemeinsam(tmp_path):
    path = tmp_path / "verlauf.csv"
    w.log_events([w.Event("wieder_da", "brinkmann", w.Item(k, k, "", "", "u", True)) for k in "abcdef"],
                 "2026-10-22T12:00:00+00:00", {"brinkmann": "Brinkmann Finest"}, path)
    hist = tmp_path / "historie.csv"
    hist.write_text("zeit_utc;zeit_berlin;wochentag;stunde;shop;artikel\n"
                    "2026-03-05T12:00:00+00:00;2026-03-05 14:00;Do;14;Brinkmann Finest;Cohiba Siglo I\n",
                    encoding="utf-8")
    text = a.bericht(a.lade(path), a.lade_historie(hist))
    assert "### Brinkmann Finest (7 Funde, davon 1 historisch)" in text
    assert "| Brinkmann Finest | 6 | 0 | 0 | 1 | – |" in text   # Wareneingang nur aus eigenem Verlauf
