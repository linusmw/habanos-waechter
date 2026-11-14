# Habanos-Wächter: Leitfaden für Claude

Dieses Projekt überwacht deutsche Online-Shops auf kubanische Zigarren (Habanos) und schickt
Push-Nachrichten (ntfy, iPhone), wenn etwas **wieder verfügbar** oder **neu** ist. Es läuft
vollständig auf GitHub Actions in diesem öffentlichen Repo. Der Nutzer spricht Deutsch und ist
kein Entwickler: Antworten auf Deutsch, Technik in einfachen Worten.

## Grundregeln

- **Vor jedem Push `git pull --rebase origin main`.** Der Bot committet bei jedem Lauf
  („Bestand aktualisiert“), also mehrmals pro Stunde.
- **Tests vor jedem Commit:** `python -m pytest -q` (alle müssen grün sein). Für jeden neuen
  Parser gibt es eine gekürzte HTML/JSON-Probe unter `tests/fixtures/` und einen Test.
- **Keine Geheimnisse ins Repo oder in den Chat:** ntfy-Thema und Tokens liegen in GitHub-Secrets
  bzw. bei cron-job.org. Der Nutzer soll Tokens nie in den Chat kopieren.
- **Bot-Schutz nicht umgehen** (Cloudflare usw.). Ein Shop, der blockt, wird ausgelassen.
- **Keine Herkunftsangaben in `historie.csv`** oder sonst im Repo darüber, woher die
  historischen Fundmeldungen stammen; keine Personennamen oder Nachrichtentexte speichern.
- Shops schonend abfragen (`PAUSE_BETWEEN_PAGES = 1.5` s, eigener User-Agent).
- Die Arbeitsumgebung von Claude erreicht die Shops oft nicht direkt (Proxy). Shop-Seiten
  untersucht man dann im Browser-Fenster der Claude-App; geprüft wird live über einen
  Workflow-Lauf (siehe „Lauf auslösen“).

## Dateien

| Datei | Inhalt |
|---|---|
| `waechter.py` | Hauptprogramm: Parser und Abrufe je Shop, Vergleich mit dem letzten Stand, Push |
| `state.json` | letzter Bestand je Shop (vom Bot gepflegt, nicht von Hand ändern ohne Grund) |
| `verlauf.csv` | jedes Ereignis mit Zeit (`;`-getrennt): `zeit_utc;zeit_berlin;wochentag;stunde;shop;ereignis;artikel;packung;preis;verfuegbar_stunden;url;schluessel` |
| `auswertung.py` → `auswertung.md` | Musterauswertung (Wochentage, Tageszeiten, Wareneingänge, Prognose, Haltbarkeit, Neuheiten); läuft nach jedem Lauf |
| `historie.csv` | 273 gesammelte historische Funde 2023–2026 (`zeit_utc;zeit_berlin;wochentag;stunde;shop;artikel`), fließen in die Zeitmuster ein |
| `neuheiten.py` → `neuheiten.csv` | wann Shops Produkte neu angelegt / wieder veröffentlicht haben (Zeitstempel der Shopsysteme) |
| `wunschliste.txt` | Wunsch-Zigarren mit höchster Priorität (61 Einträge) |
| `.github/workflows/waechter.yml` | der Workflow |
| `tests/` | pytest; `conftest.py` leitet `STATE_FILE`, `LOG_FILE`, `WISH_FILE` in ein Temp-Verzeichnis um |

## Die 21 Shops (`SHOPS` in waechter.py)

Modus `voll` = Seite zeigt auch Ausverkauftes; `nur_lieferbar` = Liste enthält nur Lieferbares
(fehlt etwas, gilt es als ausverkauft; Neues heißt dann „neu oder wieder da“);
`nur_neu` = kein Lagerstand sichtbar, nur Neuheiten. ⚡ = auch in der 5-Minuten-Nachprüfung.

| id | Name | System / Quelle | Modus |
|---|---|---|---|
| zigarrentraum | Zigarrentraum | WooCommerce Store API (Varianten je Packung) | voll |
| casabenden ⚡ | Casa Benden | eigene Seite, `?nur_lieferbar=1`, Blättern über `von=` | nur_lieferbar |
| brinkmann ⚡ | Brinkmann Finest | Kategorie Kuba (listet nur Kaufbares) | nur_lieferbar |
| noblego | Noblego | Magento | voll |
| zigarrede | zigarre.de | Magento | voll |
| wolsdorff ⚡ | Wolsdorff | Shopware 6 (`is-out-of-stock`) | voll |
| tabacstore ⚡ | Tabacstore | Shopware 6, `?immediately-available=1` | nur_lieferbar |
| dalay ⚡ | Dalay Zigarren | JTL NOVA, `?availability=1`, Seiten `_sN` | nur_lieferbar |
| peterheinrichs | Peter Heinrichs | Shopware 6, `order=name-asc` | voll |
| falkum | Tabakhaus Falkum | Magento | voll |
| ccigars ⚡ | c-cigars | Shopify `products.json` | voll |
| habanoszigarren | habanos-zigarren.com | ePages REST `/rs/shops/62530561/products` | voll |
| lagalana ⚡ | La Galana | Shopify | voll |
| dominique | Dominique London (ehem. Casa del Puro) | Odoo | nur_neu |
| starkezigarren ⚡ | StarkeZigarren | JTL NOVA (nur `#product-list`, Slider ignorieren) | nur_lieferbar |
| pipehouse | Pipe House | WooCommerce Store API, Kategorie-ID 10856, X-WP-Total fehlerhaft, Marke aus `brands` | voll |
| nobbis ⚡ | Nobbis Tabakwelt | WooCommerce Store API, Kategorie-ID 53 | voll |
| paulbugge | Paul Bugge | eigenes System, `?page=N`, Punkt `forestgreen` = lieferbar, `red` = nicht | voll |
| cigarrenversand24 | Cigarrenversand24 | OXID: „Habanos-Specialist“-Liste + handgepflegte Liste „Unsere VERFÜGBAREN Habanos“, zusammengeführt über `anid` | voll |
| tabakkontor | Tabak-Kontor Leipzig | VirtueMart, Kategorie `cuba` → Marken → Linien durchlaufen | voll |
| herzog ⚡ | Zigarren Herzog | Shopify, Marke aus `vendor` voranstellen | voll |

**Bewusst nicht dabei:** Zigarrenschachtel (Cloudflare blockt GitHub; Code auskommentiert),
CIGARWORLD (gleiche Firma und gleiches Lager wie Casa Benden → doppelte Meldungen;
`fetch_cigarworld` existiert noch, ist nicht in `SHOPS`).

Neuen Shop hinzufügen: `parse_…` + `fetch_…` (oder vorhandene Fabriken wie
`make_shopify_fetcher`, `make_woo_fetcher`, `make_jtl_fetcher`), Eintrag in `SHOPS` und
`SHOP_URLS` (Klickziel der Nachricht), Fixture + Test, README-Liste. Kandidaten, die noch
offen sind: Pfeifen Huber, Tabak Träber, Cigarren Schum, Tabak Brucker, onlineshop-helgoland,
La Casa del Habano Hamburg (Duske & Duske), Die TabakGalerie, Cigarrencontor, Zigarrenwelt,
Rauchr, Veicht, Dürninger. Vorher prüfen, ob Havannas mit Lagerstand online stehen.

## Ablauf eines Laufs

1. Jeder Shop wird gelesen → Liste von `Item(key, name, pack, price, url, available, group)`.
2. Filter `is_wanted()`: Zubehör (`ACCESSORY_WORDS`: Feuerzeug, Humidor, Etui, Jar, leer, Buch …,
   außer „mit N Zigarren“ / „5er Metalletui“ / „25er Jar“) und Nicht-Havannas
   (`NON_HABANOS`: VegaFina, Maria Mancini) fliegen bei allen Shops raus.
3. `plausible()` schützt vor halb geladenen Seiten (Vergleich mit `last_count`); Fehler ändern
   den Bestand nicht. Nach 6 Fehlläufen in Folge gibt es eine Warnung, dann eine Entwarnung.
4. `compare()` erzeugt Ereignisse: `wieder_da`, `neu`, `neu_oder_wieder`, `neu_nicht_lieferbar`,
   `ausverkauft` (nur protokolliert, nie gepusht; mit `verfuegbar_stunden`).
   Der erste Lauf eines Shops ist nur Ausgangsbestand.
5. `log_events()` → `verlauf.csv`; `notify()` → **eine Nachricht pro Shop** (`shop_message()`):
   alle Zigarren mit Packungen und Preisen, max. 3800 Zeichen.
6. **Wunschliste:** Treffer stehen oben unter „⭐ Wunschliste“, Titel beginnt mit ⭐,
   Priorität 5. Sonst: wieder da/neu Priorität 4, nur gelistet Priorität 3.
7. Mit `--nachpruefen` (bei allen cron-job.org-Läufen): danach `FAST_SHOPS` alle 5 Minuten bis
   Minute 26 der halben Stunde (`recheck_fast_shops`). Ein Lauf dauert daher ~25 Minuten.

### Wunschliste (`wunschliste.txt`)

Eine Zeile je Zigarre, `#` = Kommentar. Treffer, wenn **jedes** Wort im Namen vorkommt;
Groß-/Kleinschreibung und Akzente egal; Wörter ab 3 Buchstaben dürfen Wortanfang sein
(„Corona“ → „Coronas“); Zahlen exakt; `N°2`/`Nr. 2` → `no 2`; Packungsangaben wie `1'S`, `10er`
zählen nicht. `-Wort` schließt aus („Montecristo Edmundo -Petit“). „Conno“ deckt beide
Schreibweisen von Connoisseur ab, „A T“ steht für A/T. Stand anzeigen:
`python waechter.py --wuensche`.

Der Nutzer pflegt seine Auswahl auf einer privaten Artifact-Seite „Habanos-Wunschliste“
(Speicher-Dokument `wunsch/auswahl`). Bei „Wunschliste übernehmen“: dort lesen und daraus kurze,
shopübergreifende Einträge für `wunschliste.txt` bauen, Treffer gegen `state.json` prüfen.

## Betrieb

- **Takt:** cron-job.org ruft alle 30 Minuten (:00/:30 deutscher Zeit) per
  `POST https://api.github.com/repos/linusmw/habanos-waechter/actions/workflows/waechter.yml/dispatches`
  mit Body `{"ref":"main"}` auf. Header: `Accept: application/vnd.github+json`,
  `Authorization: Bearer <token>`, `X-GitHub-Api-Version: 2022-11-28`,
  `Content-Type: application/json`.
- **Token:** Fine-grained, nur „Actions: Read and write“ für dieses Repo,
  **läuft am 30.09.2027 ab** → neu erstellen und in cron-job.org eintragen
  (Ablaufdatum beim Erstellen höchstens ein Jahr in die Zukunft, sonst tut der Knopf nichts).
  API-Version 2022-11-28 wird laut GitHub ab 10.03.2028 nicht mehr unterstützt.
- **Reserve:** GitHub-Zeitplan `11 */3 * * *` (eigene Concurrency-Gruppe pro Ereignistyp,
  ohne Nachprüfung); dabei läuft auch `neuheiten.py`.
- **Speichern:** Wird der Push abgelehnt (anderer Lauf hat inzwischen gespeichert), holt der
  Schritt den neuen Stand mit `git pull --rebase -X theirs` und versucht es bis zu 3×.
  Eigene Pushes während eines laufenden Laufs sind dadurch unkritisch.
- **Secrets:** `NTFY_TOPIC` (Thema in der ntfy-App des Nutzers), optional `NTFY_TOKEN`.
- **Workflow-Eingaben:** `test_push` (nur Testnachricht), `neuheiten` (Neuheiten sofort).

### Lauf auslösen und prüfen

```bash
gh api -X POST repos/linusmw/habanos-waechter/actions/workflows/waechter.yml/dispatches \
  -f ref=main -F 'inputs[test_push]=false'
gh api "repos/linusmw/habanos-waechter/actions/workflows/waechter.yml/runs?per_page=5" \
  -q '.workflow_runs[]|"\(.created_at) \(.event) \(.status) \(.conclusion)"'
```

Die Logs einzelner Läufe sind über den Proxy oft nicht abrufbar; den Erfolg erkennt man an
`conclusion`, neuen Commits und dem Inhalt von `state.json`/`verlauf.csv` nach `git pull`.
Gesundheitscheck: in `state.json` je Shop `last_ok_day`, `failures`, `last_error`.

### Lokal

```bash
pip install -r requirements.txt
python waechter.py --dry-run      # nur anzeigen
python waechter.py --status       # verfügbare Artikel
python waechter.py --wuensche     # Stand der Wunschliste
python auswertung.py              # Auswertung auf die Konsole
python neuheiten.py --dry-run
python -m pytest -q
```

Commits im Namen des Nutzers:
`git -c user.name="linusmw" -c user.email="linusmw@users.noreply.github.com" commit …`

## Entscheidungen und Verlauf (damit nichts doppelt diskutiert wird)

- 20.10.2026 Start mit 3 Shops, schnell auf 14, am 23.10. auf 21 erweitert.
- Eine Nachricht **pro Shop** statt pro Zigarre (Wunsch des Nutzers).
- Zubehör- und Nicht-Havanna-Filter für alle Shops (23.10.).
- 5-Minuten-Nachprüfung (25.10.): Anlass war eine Bolívar Royal Coronas bei Wolsdorff, die
  zwischen zwei 30-Minuten-Läufen kam und wieder ging (der Nutzer hat sie gekauft).
  Nobbis kam am 04.11. dazu.
- **Keine Ruhezeit für Wiederholungen:** am 04.11. eingebaut und auf Wunsch des Nutzers sofort
  wieder entfernt. Jede Rückkehr wird gemeldet.
- Zigarrentraum: eine mehrtägige Sonderaktion (13.–15.10.2026, jeden Tag eine andere Zigarre)
  ist aus `historie.csv` herausgenommen, weil sie das Muster verfälscht.

## Bekannte Muster (Stand Mitte November 2026)

- **Wolsdorff:** Wareneingang **freitags gegen 13 Uhr**, vier Wochen in Folge (23.10., 30.10.,
  06.11., 13.11.), 7–9 Zigarren; Ware teils nach Minuten weg (Ramón Allones No. 3: 6 Minuten).
  Gelegentlich auch Montagmorgen.
- Über alle Shops: Donnerstag und Freitag sind die stärksten Tage, meist 12–15 Uhr.
- Nobbis: Eingänge um 13:30 Uhr unter der Woche. Casa Benden: einzelne Restbestände.
  Cigarrenversand24: Bestand springt oft zwischen verfügbar und ausverkauft.
- Neuheiten (Shopsystem-Zeitstempel): Zigarrentraum meist dienstags, La Galana freitags
  nachmittags; „wieder online“ bei c-cigars oft mittwochabends.

## Zusätzliche Seiten des Nutzers (claude.ai-Artifacts, privat)

- **Habanos-Nachschubmuster:** Grafik-Seite (Wochentag/Uhrzeit je Shop, Neuheiten). Die Daten
  sind eine Momentaufnahme als hochgeladene CSV; zum Aktualisieren `funde.csv` (historie.csv +
  Zugänge aus verlauf.csv, Casa Benden und CIGARWORLD zusammengefasst) und `neuheiten.csv` neu
  hochladen und die Datensätze `funde`/`neuheiten` umstellen.
- **Habanos-Wunschliste:** Auswahlseite mit allen gelisteten Habanos (Sterne), siehe oben.
