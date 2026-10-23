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
    ev = w.compare(CB, st, [], "t1")                    # ausverkauft -> nur Protokoll
    assert [(e.kind, e.item.key) for e in ev] == [("ausverkauft", "x")]
    assert st["items"]["x"]["available"] is False
    ev = w.compare(CB, st, [mk("x", True)], "t2")     # wieder da
    assert [(e.kind, e.item.key) for e in ev] == [("wieder_da", "x")]


def test_sold_out_then_new_item_is_new():
    st = {}
    w.compare(CB, st, [mk("x", True)], "t0")
    ev = w.compare(CB, st, [mk("x", True), mk("y", True)], "t1")
    assert [(e.kind, e.item.key) for e in ev] == [("neu_oder_wieder", "y")]


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
    assert w.describe_group(groups[0]) == "• Joyitas: 1er 10,00 €, 25er Kiste 10,00 €"


def test_one_message_per_shop():
    names = {s.id: s.name for s in w.SHOPS}
    ev = [w.Event("wieder_da", "noblego", mk("a1", True, "Cohiba Siglo VI", "10er", group="a")),
          w.Event("wieder_da", "noblego", mk("a2", True, "Cohiba Siglo VI", "Einzeln", group="a")),
          w.Event("wieder_da", "noblego", mk("b", True, "Partagas Serie D No.4", "25er")),
          w.Event("neu", "noblego", mk("c", True, "Trinidad EL 2026", "12er")),
          w.Event("neu_nicht_lieferbar", "noblego", mk("d", False, "Cohiba EL 2026", ""))]
    title, msg, url, tags, prio = w.shop_message("noblego", ev, names)
    assert title == "Noblego: 2 wieder da, 1 neu, 1 neu gelistet"
    assert msg == ("✅ Wieder da\n"
                   "• Cohiba Siglo VI: 10er 10,00 €, Einzeln 10,00 €\n"
                   "• Partagas Serie D No.4: 25er 10,00 €\n\n"
                   "🆕 Neu\n• Trinidad EL 2026: 12er 10,00 €\n\n"
                   "👀 Neu gelistet, noch nicht lieferbar\n• Cohiba EL 2026: 10,00 €")
    assert url == w.SHOP_URLS["noblego"] and prio == 5


def test_single_product_links_to_product():
    names = {s.id: s.name for s in w.SHOPS}
    ev = [w.Event("neu_oder_wieder", "casabenden", mk("x", True, "Trinidad Reyes", ""))]
    title, _, url, _, _ = w.shop_message("casabenden", ev, names)
    assert title == "Casa Benden: 1 neu/wieder da" and url == "https://x/x"


def test_long_message_is_truncated():
    names = {s.id: s.name for s in w.SHOPS}
    ev = [w.Event("wieder_da", "ccigars", mk(f"k{i}", True, f"Zigarre mit langem Namen Nr. {i}", "25er Kiste"))
          for i in range(200)]
    _, msg, _, _, _ = w.shop_message("ccigars", ev, names)
    assert len(msg) <= w.NTFY_MAX_CHARS + 60 and msg.rstrip().endswith("weitere (siehe Shop)")


def test_notify_sends_one_push_per_shop(monkeypatch):
    sent = []
    monkeypatch.setattr(w, "push", lambda title, message, **kw: sent.append(title) or True)
    names = {s.id: s.name for s in w.SHOPS}
    ev = [w.Event("wieder_da", "noblego", mk(f"n{i}", True, f"Zigarre {i}")) for i in range(10)]
    ev += [w.Event("wieder_da", "falkum", mk("f", True, "Cohiba Siglo I")),
           w.Event("ausverkauft", "wolsdorff", mk("x", False))]
    w.notify(ev, names)
    assert sent == ["Noblego: 10 wieder da", "Tabakhaus Falkum: 1 wieder da"]

def test_full_run(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(w, "LOG_FILE", tmp_path / "verlauf.csv")
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
    assert pushes == ["Zigarrentraum: 1 wieder da"]   # "ausverkauft" pusht nicht
    log = (tmp_path / "verlauf.csv").read_text(encoding="utf-8").splitlines()
    assert log[0].startswith("zeit_utc;zeit_berlin;wochentag;stunde;shop;ereignis")
    assert any(";Zigarrentraum;wieder_da;Montecristo No.4;" in row for row in log)
    assert any(";Casa Benden;ausverkauft;Trinidad Reyes;" in row for row in log)

    stock["casabenden"] = [mk("r", True, "Trinidad Reyes")]
    for _ in range(4):
        w.run()
    assert "Casa Benden: 1 wieder da" in pushes
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


# ---------------------------------------------------------------- Fachhändler

def test_peterheinrichs_parser():
    items = w.parse_shopware_listing(read("peterheinrichs_page.html"))
    assert [(i.key, i.name, i.available, i.price) for i in items] == [
        ("a30e9a5979934d99bca0010000029081", "Trinidad Wide Short", False, "19,80 €"),
        ("a30e9a5979934d99bca0010000025772", "H.Upmann Jar Humidor", True, "270,00 €"),
    ]


def test_falkum_parser():
    items, total = w.parse_falkum(read("falkum_page.html"))
    assert total == 239
    assert [(i.key, i.name, i.available, i.price) for i in items] == [
        ("AC-PJPAR", "Habanos Jar Partagas", True, "270,00 €"),
        ("ZI-HAQD50A", "Quai d'Orsay Especial d'Orsay (50 Anos)", False, "50,00 €"),
    ]


def test_shopify_parser():
    items = w.parse_shopify(json.loads(read("shopify_products.json")), "https://c-cigars.de")
    assert [(i.name, i.pack, i.price, i.available) for i in items] == [
        ("Cohiba White Mini Zigarillos", "", "18,90 €", True),
        ("Montecristo Short", "Einzeln verpackt", "4,50 €", True),
        ("Montecristo Short", "10er Kiste", "45,00 €", False),
        ("Romeo y Julieta - No. 1 Tubos", "", "17,30 €", False),
    ]
    assert items[0].url == "https://c-cigars.de/products/cohiba-white-mini-20-stuck"
    assert items[2].url.endswith("?variant=47378710820002")
    assert items[1].group == items[2].group


def test_epages_parser():
    items = w.parse_epages(json.loads(read("epages_products.json")))
    assert [(i.name, i.available, i.price) for i in items] == [
        ("Cohiba Club", True, "20,60 €"), ("Bolivar Belicosos Finos", False, "13,20 €")]
    assert items[0].url.startswith("https://www.habanos-zigarren.com/epages/")


def test_odoo_parser():
    items = w.parse_odoo(read("dominique_page.html"))
    assert [(i.key, i.name, i.price, i.available) for i in items] == [
        ("21346", "Partagas Serie Mini - Pack Of 20 Cigarillos", "11,20 €", True),
        ("21339", "Partagas Linea Maestra Maestro - Box Of 20 Cigars", "1.700,00 €", True),
    ]
    assert items[0].url == "https://www.dominiquelondon.de/shop/21346-partagas-serie-mini-pack-of-20-cigarillos-21346"


def test_news_only_mode():
    shop = next(s for s in w.SHOPS if s.mode == "nur_neu")
    st = {}
    w.compare(shop, st, [mk("a", True)], "t0")
    assert w.compare(shop, st, [], "t1") == []                    # Seite fehlt mal -> nichts
    assert st["items"]["a"]["available"] is True
    assert w.compare(shop, st, [mk("a", True)], "t2") == []       # kein "wieder da"
    ev = w.compare(shop, st, [mk("a", True), mk("b", True)], "t3")
    assert [(e.kind, e.item.key) for e in ev] == [("neu", "b")]


# ---------------------------------------------------------------- Verlauf

def test_duration_until_sold_out():
    st = {}
    w.compare(ZT, st, [mk("a", False)], "2026-10-20T08:00:00+00:00")
    w.compare(ZT, st, [mk("a", True)], "2026-10-20T08:30:00+00:00")          # wieder da
    ev = w.compare(ZT, st, [mk("a", False)], "2026-10-21T10:00:00+00:00")    # 25,5 h später weg
    assert [(e.kind, e.hours_available) for e in ev] == [("ausverkauft", 25.5)]


def test_baseline_stock_has_unknown_duration():
    st = {}
    w.compare(CB, st, [mk("a", True)], "2026-10-20T08:00:00+00:00")         # Ausgangsbestand
    ev = w.compare(CB, st, [], "2026-10-20T12:00:00+00:00")
    assert ev[0].kind == "ausverkauft" and ev[0].hours_available is None


def test_log_file_format(tmp_path):
    path = tmp_path / "verlauf.csv"
    ev = [w.Event("wieder_da", "noblego", mk("k", True, "Cohiba Siglo VI", "10er")),
          w.Event("ausverkauft", "noblego", mk("k", False, "Cohiba Siglo VI", "10er"), 3.5)]
    w.log_events(ev, "2026-10-23T18:26:00+00:00", {"noblego": "Noblego"}, path)
    w.log_events(ev[:1], "2026-10-24T06:56:00+00:00", {"noblego": "Noblego"}, path)
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4                                   # Kopfzeile nur einmal
    assert rows[1].split(";")[:7] == ["2026-10-23T18:26:00+00:00", "2026-10-23 20:26", "Fr", "20",
                                      "Noblego", "wieder_da", "Cohiba Siglo VI"]
    assert rows[2].split(";")[9] == "3,5"


def test_available_only_shop_new_item_is_new_or_back():
    st = {}
    w.compare(CB, st, [mk("x", True)], "t0")
    ev = w.compare(CB, st, [mk("x", True), mk("y", True)], "t1")
    assert [(e.kind, e.item.key) for e in ev] == [("neu_oder_wieder", "y")]
    title, _, _, _, _ = w.shop_message("casabenden", ev, {s.id: s.name for s in w.SHOPS})
    assert title == "Casa Benden: 1 neu/wieder da"


def test_cigarworld_ignores_carousel():
    items, total = w.parse_casabenden_lieferbar(read("cigarworld_lieferbar.html"),
                                                "https://www.cigarworld.de")
    assert total == 7
    assert [(i.name, i.price) for i in items] == [
        ("Trinidad Reyes", "34,00 €"),
        ("Romeo y Julieta De Oro Hidalgos", "65,00 €"),
    ]
    assert items[0].url == "https://www.cigarworld.de/zigarren/kuba/regulares/trinidad-reyes-01021_5800"
    assert "Restbestand" in items[1].pack


def test_starkezigarren_ignores_slider():
    items = w.parse_jtl_list(read("starkezigarren_lieferbar.html"))
    assert [(i.name, i.price) for i in items] == [
        ("Partagas Linea Maestra Geschenkbox", "450,00 €"),
        ("Trinidad Short", "ab 1,70 €"),
    ]


def test_every_shop_has_click_url():
    assert {s.id for s in w.SHOPS} <= set(w.SHOP_URLS)


def test_shopify_brand_from_vendor():
    data = {"products": [{"id": 1, "handle": "reyes", "title": "Reyes", "vendor": "Trinidad",
                          "variants": [{"id": 9, "title": "Einzelzigarre", "price": "17.50",
                                        "available": True}]},
                         {"id": 2, "handle": "x", "title": "Cohiba Siglo VI", "vendor": "Cohiba",
                          "variants": [{"id": 8, "title": "Default Title", "price": "60",
                                        "available": False}]}]}
    items = w.parse_shopify(data, "https://zigarren-herzog.com", brand_from_vendor=True)
    assert [i.name for i in items] == ["Trinidad Reyes", "Cohiba Siglo VI"]
    assert w.parse_shopify(data, "https://c-cigars.de")[0].name == "Reyes"


def test_accessory_filter():
    names = ["Dupont Megajet Feuerzeug Cohiba 55", "Dupont Humidor Cohiba 55",
             "Lederetui Cohiba 2020 - 2 Cigars", "Trinidad Short", "Partagas Linea Maestra Geschenkbox",
             "Cohiba Short Year of the Snake 88er-Kiste"]
    assert [n for n in names if not w.is_accessory(mk("x", True, n))] == [
        "Trinidad Short", "Partagas Linea Maestra Geschenkbox", "Cohiba Short Year of the Snake 88er-Kiste"]
