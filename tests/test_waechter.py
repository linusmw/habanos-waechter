import json
from pathlib import Path

import pytest

import waechter as w

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- Parser

def test_zigarrentraum_parser():
    products = json.loads(read("zigarrentraum_products.json"))
    variations = json.loads(read("zigarrentraum_variations.json"))
    items = w.parse_zigarrentraum_api(products, variations)
    assert len(items) == 9
    joy = [i for i in items if i.name == "Montecristo Joyitas"]
    assert [(i.pack, i.price, i.available) for i in joy] == [
        ("1er", "11,00 €", True),
        ("25er Kiste", "275,00 €", False),
    ]
    assert joy[0].key == "16190" and joy[0].group == "16189"
    assert joy[0].url.endswith("/montecristo-joyitas/")
    upmann = [i for i in items if i.name == "H.Upmann Half Corona"]
    assert [i.pack for i in upmann] == ["1er", "25er Kiste", "1er aus der Blechdose", "5er Blechdose"]
    assert not any(i.available for i in upmann)
    quai = [i for i in items if i.key == "13575"]
    assert quai[0].name == "Quai D’orsay Coronas Claro"   # HTML-Entity aufgelöst
    assert quai[0].price == "12,00 €" and not quai[0].available


def test_zigarrentraum_missing_variations_is_error():
    products = json.loads(read("zigarrentraum_products.json"))
    with pytest.raises(w.FetchError):
        w.parse_zigarrentraum_api(products, [])   # Joyitas ist vorrätig, Varianten fehlen


def test_casabenden_parser():
    items, total = w.parse_casabenden_lieferbar(read("casabenden_lieferbar.html"))
    assert total == 6
    assert len(items) == 4
    reyes = items[0]
    assert reyes.name == "Trinidad Reyes"
    assert reyes.price == "34,00 €"
    assert reyes.available
    assert reyes.url == "https://www.casabenden.de/zigarren/kuba/regulares/trinidad-reyes-01021_5800"
    assert items[1].name == "Cohiba Edición Limitada 55 Aniversario (2021)"
    assert "Restbestand" in items[1].pack
    assert items[2].price == "ab 80,00 €" and items[2].pack == "3 Varianten"
    assert items[3].price == "476.000,00 €"


def test_brinkmann_parser():
    items = w.parse_brinkmann(read("brinkmann_list.html"))
    assert [i.key for i in items] == ["SW11544", "SW10659", "SW11032"]
    assert items[0].name == "Romeo y Julieta Linea de ORO Dianas Zigarre 20er Kiste"
    assert items[0].price == "1.183,40 €"
    assert items[0].pack == "20 Stück"
    assert all(i.available for i in items)


# ---------------------------------------------------------------- Vergleich

def mk(key, avail, name="Testzigarre", pack="1er", group=None):
    return w.Item(key=key, name=name, pack=pack, price="10,00 €", url="https://x/" + key,
                  available=avail, group=group or key)


ZT = w.SHOPS[0]   # Modus "voll"
CB = w.SHOPS[1]   # Modus "nur_lieferbar"


def test_first_run_no_alerts():
    st = {}
    ev = w.compare(ZT, st, [mk("a", True), mk("b", False)], "t0")
    assert ev == [] and st["initialized"]


def test_back_in_stock_and_new():
    st = {}
    w.compare(ZT, st, [mk("a", False), mk("b", True)], "t0")
    ev = w.compare(ZT, st, [mk("a", True), mk("b", True), mk("c", False, name="Neu EL 2026")], "t1")
    kinds = sorted((e.kind, e.item.key) for e in ev)
    assert kinds == [("neu_nicht_lieferbar", "c"), ("wieder_da", "a")]
    # derselbe Stand erneut: keine doppelten Alarme
    assert w.compare(ZT, st, [mk("a", True), mk("b", True), mk("c", False)], "t2") == []


def test_available_only_shop_disappear_and_return():
    st = {}
    w.compare(CB, st, [mk("x", True)], "t0")
    assert w.compare(CB, st, [], "t1") == []          # ausverkauft -> kein Alarm
    assert st["items"]["x"]["available"] is False
    ev = w.compare(CB, st, [mk("x", True)], "t2")     # wieder da
    assert [(e.kind, e.item.key) for e in ev] == [("wieder_da", "x")]


def test_sold_out_then_new_item_is_new():
    st = {}
    w.compare(CB, st, [mk("x", True)], "t0")
    ev = w.compare(CB, st, [mk("x", True), mk("y", True)], "t1")
    assert [(e.kind, e.item.key) for e in ev] == [("neu", "y")]


def test_plausibility_guards():
    st = {"items": {}, "last_count": 549}
    assert w.plausible(ZT, st, [mk(str(i), False) for i in 100 * [0]][:100])
    assert w.plausible(ZT, st, [mk(str(i), False) for i in range(540)]) is None
    st_cb = {"items": {str(i): {"available": True} for i in range(6)}}
    assert w.plausible(CB, st_cb, []) is not None       # plötzlich alles weg -> verdächtig
    st_cb2 = {"items": {"a": {"available": True}}}
    assert w.plausible(CB, st_cb2, []) is None          # 1 -> 0 ist normal


def test_grouping_by_product():
    events = [w.Event("wieder_da", "zigarrentraum", mk("v1", True, "Joyitas", "1er", group="p1")),
              w.Event("wieder_da", "zigarrentraum", mk("v2", True, "Joyitas", "25er Kiste", group="p1")),
              w.Event("neu", "casabenden", mk("z", True, "Cohiba EL"))]
    groups = w.group_events(events)
    assert len(groups) == 2
    title, msg, url, _ = w.describe(groups[0], {s.id: s.name for s in w.SHOPS})
    assert title == "Wieder da bei Zigarrentraum: Joyitas"
    assert "1er" in msg and "25er Kiste" in msg


# ---------------------------------------------------------------- Gesamtlauf

def test_full_run(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(w, "PAUSE_BETWEEN_PAGES", 0)
    pushes = []
    monkeypatch.setattr(w, "push", lambda title, message, **kw: pushes.append(title) or True)

    stock = {
        "zigarrentraum": [mk("a", False, "Montecristo No.4"), mk("b", True, "Joyitas")],
        "casabenden": [mk("r", True, "Trinidad Reyes")],
        "brinkmann": "boom",
    }

    def fake(shop_id):
        def f(session):
            data = stock[shop_id]
            if data == "boom":
                raise w.FetchError("HTTP 404")
            return data
        return f

    shops = [w.Shop(s.id, s.name, s.mode, fake(s.id)) for s in w.SHOPS[:3]]
    monkeypatch.setattr(w, "SHOPS", shops)

    assert w.run() == 0                       # Ausgangsbestand
    assert pushes == []
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["shops"]["zigarrentraum"]["initialized"]
    assert state["shops"]["brinkmann"]["failures"] == 1

    stock["zigarrentraum"][0] = mk("a", True, "Montecristo No.4")
    stock["casabenden"] = []
    assert w.run() == 0
    assert pushes == ["Wieder da bei Zigarrentraum: Montecristo No.4"]

    stock["casabenden"] = [mk("r", True, "Trinidad Reyes")]
    for _ in range(4):
        w.run()
    assert "Wieder da bei Casa Benden: Trinidad Reyes" in pushes
    assert "Problem mit Brinkmann Finest" in pushes   # nach 6 Fehlläufen genau eine Warnung
    assert pushes.count("Problem mit Brinkmann Finest") == 1

    stock["brinkmann"] = [mk("s1", True, "Cohiba Panatelas")]
    w.run()
    assert "Brinkmann Finest wieder erreichbar" in pushes
    # erster erfolgreicher Brinkmann-Lauf = Ausgangsbestand, kein "neu"-Alarm
    assert not any("Panatelas" in p for p in pushes)


def test_zigarrentraum_null_attributes():
    products = json.loads(read("zigarrentraum_products.json"))
    items = w.parse_zigarrentraum_api(products, json.loads(read("zigarrentraum_variations.json")))
    medio = {i.key: i for i in items if i.name == "Cohiba Medio Siglo"}
    assert medio["23839"].pack == ""             # Wert fehlt im Shop -> leer, kein Absturz
    assert medio["23840"].pack == "25er Kiste"   # leere "Intensität" wird übersprungen


# ---------------------------------------------------------------- neue Shops

def test_noblego_parser():
    items, total = w.parse_noblego(read("noblego_page.html"))
    assert total == 417
    by = {(i.name, i.pack): i for i in items}
    assert by[("Cohiba White Club Cigarillos", "20er")].available
    assert by[("Cohiba White Club Cigarillos", "20er")].price == "22,21 €"
    assert not by[("Bolivar Belicoso Fino", "25er")].available
    assert by[("Bolivar Belicoso Fino", "Einzeln")].available            # Packung einzeln auf Lager
    assert by[("Bolivar Belicoso Fino", "25er")].price == "523,80 €"
    assert not by[("Bolivar Coronas Gigantes", "Einzeln")].available     # "aufgeraucht"
    assert by[("Ohne Packungsliste", "")].price == "9,90 €"
    assert by[("Bolivar Belicoso Fino", "Einzeln")].key == "https://www.noblego.de/bolivar-belicoso-fino/#Einzeln"
    assert len(items) == 6


def test_zigarrede_parser():
    items, total = w.parse_zigarrede(read("zigarrede_page.html"))
    assert total == 174
    assert [(i.key, i.name, i.available, i.price) for i in items] == [
        ("1768", "Trinidad Reyes", True, "ab 34,00 €"),
        ("4838", "Guantanamera Cristales", False, "ab 2,65 €"),
    ]   # das Widget "Villiger Virginia" gehört nicht zur Kategorie


def test_wolsdorff_parser():
    items = w.parse_shopware_listing(read("wolsdorff_page.html"))
    assert [(i.name, i.available, i.price) for i in items] == [
        ("Cohiba 55 Aniversario Edición Limitada 2021", True, "ab 330,80 €"),
        ("Bolivar Belicosos Finos Single 1'S", False, "21,60 €"),
    ]
    assert items[1].key == "69e5c7fdf70850e272d3d879bd81a1d3"


def test_tabacstore_parser():
    items = w.parse_tabacstore(read("tabacstore_lieferbar.html"))
    got = [(i.name, i.pack, i.available) for i in items]
    assert got == [
        ("Trinidad Media Luna", "12er Gebinde", True),
        ("Trinidad Media Luna", "Einzel", False),
        ("Cohiba 1492 Siglo II", "5er Gebinde", True),
        ("Cohiba 1492 Siglo II", "25er Kiste", False),
        ("Cohiba 1492 Siglo II", "Einzel", True),
        ("Testprodukt ohne Varianten", "", True),
    ]
    assert items[0].key == "https://www.tabacstore.de/trinidad-media-luna/fa6452120.1"
    assert items[0].price == "516,00 €" and items[1].price == ""
    assert items[0].group == items[1].group == "0194dfc426017143bc6dab3a292d5b42"


def test_jtl_parsers():
    zs = w.parse_jtl_list(read("zigarrenschachtel_lieferbar.html"))
    assert [(i.name, i.price) for i in zs] == [
        ("Guantanamera Decimos", "ab 2,00 €"),
        ("Cohiba Siglo de Oro Ano Chino", "1.250,00 €"),
    ]
    dl = w.parse_jtl_list(read("dalay_lieferbar.html"))
    assert [(i.name, i.pack, i.price) for i in dl] == [
        ("Romeo y Julieta Short Churchills Reserva 2019 Cosecha", "nur einzelne Packungen", "ab 140,00 €"),
        ("Trinidad Wide Short", "", "19,80 €"),
    ]
    assert all(i.available for i in zs + dl)


def test_collect_pages_stops_when_shop_repeats_last_page(monkeypatch):
    pages = {1: ["a", "b"], 2: ["c"], 3: ["c"], 4: ["c"]}   # Shop wiederholt die letzte Seite
    calls = []
    monkeypatch.setattr(w, "PAUSE_BETWEEN_PAGES", 0)
    monkeypatch.setattr(w, "get", lambda s, url, allow_404=False: calls.append(url) or url)
    parse = lambda url: [mk(k, True) for k in pages[int(url)]]
    items, _ = w.collect_pages(None, lambda p: str(p), parse)
    assert [i.key for i in items] == ["a", "b", "c"] and calls == ["1", "2", "3"]
