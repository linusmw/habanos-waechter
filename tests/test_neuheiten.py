import neuheiten as n
import auswertung as a


def test_shopify_rows_new_and_republished():
    products = [
        {"title": "Reyes", "vendor": "Trinidad", "created_at": "2026-04-13T14:37:15+02:00",
         "published_at": "2026-04-13T14:37:15+02:00"},
        {"title": "Cohiba Siglo I", "vendor": "Cohiba", "created_at": "2023-10-08T11:16:29+02:00",
         "published_at": "2025-07-16T13:30:57+02:00"},
        {"title": "VegaFina Robusto", "vendor": "VegaFina", "created_at": "2026-01-01T10:00:00+01:00",
         "published_at": "2026-01-01T10:00:00+01:00"},
    ]
    rows = n.shopify_rows("Zigarren Herzog", products, brand_from_vendor=True)
    assert [(r["artikel"], r["art"], r["zeit_berlin"], r["wochentag"]) for r in rows] == [
        ("Trinidad Reyes", "neu", "2026-04-13 14:37", "Mo"),
        ("Cohiba Siglo I", "neu", "2023-10-08 11:16", "So"),
        ("Cohiba Siglo I", "wieder online", "2025-07-16 13:30", "Mi"),
    ]


def test_woo_rows_use_gmt_and_brand():
    posts = [{"id": 7, "date_gmt": "2026-08-31T07:33:36", "title": {"rendered": "35 Aniversario Edici&oacute;n Regional 2024"}}]
    rows = n.woo_rows("Pipe House", posts, {7: "Por Larranaga"})
    assert rows[0]["artikel"] == "Por Larranaga 35 Aniversario Edición Regional 2024"
    assert rows[0]["zeit_berlin"] == "2026-08-31 09:33" and rows[0]["stunde"] == 9


def test_drop_imports():
    rows = [n.row(n.parse_time(f"2023-10-08T09:{i:02d}:00Z"), "c-cigars", f"Z{i}", "neu") for i in range(20)]
    rows.append(n.row(n.parse_time("2026-02-12T12:11:28Z"), "c-cigars", "Cohiba Short 88", "neu"))
    kept, imports = n.drop_imports(rows)
    assert [r["artikel"] for r in kept] == ["Cohiba Short 88"]
    assert imports == [("c-cigars", "2023-10-08", 20)]


def test_merge_previous_keeps_deleted_products(tmp_path):
    path = tmp_path / "neuheiten.csv"
    old = [n.row(n.parse_time("2025-01-01T10:00:00Z"), "c-cigars", "Alt", "neu"),
           n.row(n.parse_time("2025-02-01T10:00:00Z"), "c-cigars", "X", "wieder online")]
    n.save(old, path)
    new = [n.row(n.parse_time("2026-03-01T10:00:00Z"), "c-cigars", "X", "wieder online")]
    merged = n.merge_previous(new, {"c-cigars"}, path)
    assert sorted((r["artikel"], r["art"], r["zeit_utc"][:10]) for r in merged) == [
        ("Alt", "neu", "2025-01-01"), ("X", "wieder online", "2026-03-01")]


def test_auswertung_neuheiten_section():
    rows = [dict(n.row(n.parse_time(f"2026-0{m}-06T13:00:00Z"), "c-cigars", f"Z{m}", "neu")) for m in range(1, 7)]
    for r in rows:
        r["stunde"] = int(r["stunde"])
    text = a.bericht([], [], rows)
    assert "## Wann kommen Neuheiten online?" in text
    assert "### c-cigars (6 Neuheiten)" in text
