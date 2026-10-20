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
