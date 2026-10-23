# Habanos-Wächter

Prüft jede halbe Stunde (zur vollen und halben Stunde, Takt über cron-job.org) den Habanos-Bestand bei

- **Zigarrentraum** (Shop-Schnittstelle, jede Packungsgröße einzeln)
- **Casa Benden** (Liste „nur sofort lieferbare Artikel“)
- **Brinkmann Finest** (Kategorie Kuba; dort ist nur gelistet, was kaufbar ist)
- **Noblego** (ganze Kuba-Kategorie, jede Packungsgröße einzeln)
- **zigarre.de** (ganze Kuba-Kategorie)
- **Wolsdorff** (ganze Kuba-Kategorie, jede Packungsgröße einzeln)
- **Dalay Zigarren** (Liste „nur lieferbar“)
- **Tabacstore** (Liste „sofort verfügbar“, jede Packungsgröße einzeln)
- **Peter Heinrichs** (ganze Kuba-Kategorie)
- **Tabakhaus Falkum** (ganze Habanos-Kategorie)
- **c-cigars** und **La Galana** (Shop-Schnittstelle, jede Packungsgröße einzeln)
- **habanos-zigarren.com** (Shop-Schnittstelle)
- **Dominique London** (ehemals Casa del Puro; der Shop zeigt keinen Lagerstand, daher nur Neuheiten)
- **StarkeZigarren** (Liste „verfügbar“)
- **Zigarren Herzog** (Shop-Schnittstelle, jede Packungsgröße einzeln)
- **Pipe House** und **Nobbis Tabakwelt** (Shop-Schnittstelle, bei Pipe House jede Packungsgröße einzeln)
- **Paul Bugge** (ganze Kuba-Kategorie)
- **Cigarrenversand24** (Habanos-Liste plus die vom Shop gepflegte Liste verfügbarer Habanos)
- **Tabak-Kontor Leipzig** (alle Marken und Linien der Kategorie Cuba)

und schickt dir **eine Push-Nachricht pro Shop** aufs Handy (mit allen Zigarren, Packungen und
Preisen darin), wenn

- eine Zigarre **wieder verfügbar** ist, oder
- ein **neues Produkt** auftaucht (bei Zigarrentraum auch, wenn es noch nicht lieferbar ist).

Bei allen Shops wird Zubehör (Feuerzeuge, leere Jars und Humidore, Ascher, Cutter, Etuis, Bücher …)
und Nicht-Havannas wie VegaFina oder Maria Mancini ignoriert. Humidore, Jars und Etuis mit Zigarren
darin („mit 50 Zigarren“, „5er Metalletui“, „25er Jar“) zählen weiter als Zigarren.

Läuft kostenlos über GitHub Actions; du brauchst keinen eigenen Rechner.

---

## Einrichtung (ca. 10 Minuten)

### 1. ntfy-App einrichten

1. Installiere die App **ntfy** (iOS App Store / Google Play, kostenlos, kein Konto nötig).
2. Tippe auf **+** und abonniere ein Thema mit einem **langen, zufälligen Namen**, z. B.
   `habanos-k7q2m9xw4t`.
   Wichtig: Wer den Namen kennt, kann mitlesen. Also nichts Erratbares wie „zigarren“.

### 2. Projekt auf GitHub anlegen

1. Melde dich bei [github.com](https://github.com) an (oder lege ein kostenloses Konto an).
2. Oben rechts **+ → New repository**, Name z. B. `habanos-waechter`.
   - **Public** empfohlen: Dann sind GitHub Actions unbegrenzt kostenlos. Im Repo stehen nur
     öffentliche Shop-Daten; dein ntfy-Thema liegt geschützt in den Secrets (Schritt 3).
   - **Private** geht auch, verbraucht aber Freiminuten (halbstündlich ca. 1.500–2.900 von 2.000 im Monat, das reicht dann nicht).
3. Lade alle Dateien dieses Ordners hoch (**Add file → Upload files**, den ganzen Ordnerinhalt
   inklusive `.github` hineinziehen) und klicke **Commit changes**.

### 3. ntfy-Thema als Secret hinterlegen

Im Repository: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `NTFY_TOPIC`
- Secret: dein Themenname aus Schritt 1, z. B. `habanos-k7q2m9xw4t`

### 4. Testen

1. Reiter **Actions** öffnen. Falls gefragt, Workflows aktivieren.
2. Links **Habanos-Wächter** wählen → **Run workflow** → Haken bei
   „Nur eine Test-Benachrichtigung senden“ → **Run workflow**.
   Nach ca. 30 Sekunden sollte die Testnachricht auf dem Handy sein.
3. Noch einmal **Run workflow**, diesmal **ohne** Haken. Dieser erste Lauf speichert den
   Ausgangsbestand in `state.json` und schickt bewusst noch keine Alarme.

Ab jetzt läuft alles jede halbe Stunde von selbst.

---

## Gut zu wissen

- **Erster Lauf je Shop = Ausgangsbestand.** Alarme gibt es erst für Änderungen danach.
- **Ausfälle:** Ist ein Shop 6 Läufe in Folge (3 Stunden) nicht lesbar (Seite umgebaut, Blockade),
  bekommst du eine Warnung und eine Entwarnung, sobald es wieder klappt. Fehlerhafte Läufe
  verändern den gespeicherten Bestand nicht, damit es keine Fehlalarme gibt.
- **Eine Nachricht pro Shop:** Kommen bei einem Shop mehrere Zigarren gleichzeitig, gibt es eine
  Sammelnachricht, z. B. „Noblego: 2 wieder da, 1 neu“, darunter jede Zigarre mit allen Packungen
  und Preisen. Antippen öffnet den Shop (bei nur einer Zigarre direkt deren Seite).
- **Aktuellen Bestand ansehen:** `state.json` im Repo öffnen, oder lokal `python waechter.py --status`.
- **GitHub pausiert geplante Workflows** in öffentlichen Repos nach 60 Tagen ohne Aktivität.
  Der Wächter speichert täglich einen kleinen Zeitstempel, das sollte reichen. Falls doch
  pausiert: unter **Actions** einfach wieder aktivieren.
- **Taktgeber:** GitHub startet geplante Läufe bei kostenlosen Projekten nur alle 2–6 Stunden.
  Den 30-Minuten-Takt liefert deshalb **cron-job.org**: Ein Cronjob ruft alle 30 Minuten per
  `POST https://api.github.com/repos/linusmw/habanos-waechter/actions/workflows/waechter.yml/dispatches`
  (Body `{"ref":"main"}`) den Wächter auf. Dazu dient ein Fine-grained Token, das nur
  „Actions: Read and write“ für dieses Projekt darf. Es läuft am **30.09.2027** ab und muss
  dann erneuert und in cron-job.org neu eingetragen werden. Der GitHub-Zeitplan alle 3 Stunden
  bleibt als Reserve.
- **API-Version:** Der Header `X-GitHub-Api-Version: 2022-11-28` wird laut GitHub ab
  10.03.2028 nicht mehr unterstützt. Vorher in cron-job.org auf die dann aktuelle Version umstellen.

## Verlauf und Muster

Jede Änderung landet mit Zeitpunkt in **`verlauf.csv`**: wieder da, neu und ausverkauft
(inklusive wie viele Stunden die Ware verfügbar war). Die Datei öffnet sich direkt in Excel
oder Numbers.

Daraus erzeugt jeder Lauf die Datei **`auswertung.md`**, auf GitHub direkt lesbar:

- an welchen **Wochentagen und Tageszeiten** jeder Shop nachfüllt
- **Wareneingänge** (mindestens 3 Artikel gleichzeitig) und ihr Rhythmus in Tagen
- eine **Prognose** für den nächsten Wareneingang, sobald ein Shop regelmäßig liefert
- wie schnell einzelne Zigarren **wieder vergriffen** sind

Zusätzlich enthält **`historie.csv`** rund 270 gesammelte Fundmeldungen aus den Jahren
2023–2026 (Zeitpunkt, Shop, Zigarre). Sie fließen in die Wochentag- und Tageszeitmuster sowie
die Liste der meistgefundenen Zigarren ein, nicht aber in Wareneingänge oder Haltbarkeit.
Zeitpunkte im eigenen Verlauf sind auf etwa 30 Minuten genau (Prüfintervall).

## Lokal ausprobieren

```bash
pip install -r requirements.txt
python waechter.py --dry-run          # zeigt Ergebnisse, speichert und sendet nichts
NTFY_TOPIC=dein-thema python waechter.py --test-push
python auswertung.py                  # Muster-Auswertung aus verlauf.csv
python -m pytest -q                   # Tests (pip install pytest)
```

## Nicht überwachbar

- **Zigarrenschachtel**: Der Bot-Schutz des Shops (Cloudflare) blockiert Abrufe von GitHub.
  Der Parser ist im Code vorhanden, aber deaktiviert.

- **CIGARWORLD**: gehört zu Casa Benden und hat dasselbe Lager; würde nur doppelt melden.
  Der Abruf ist im Code vorhanden (`fetch_cigarworld`), aber nicht aktiv.

## Einen weiteren Shop hinzufügen

In `waechter.py` eine `parse_…`- und eine `fetch_…`-Funktion schreiben und den Shop in die
Liste `SHOPS` eintragen. Modus `"voll"`, wenn die Seite auch nicht lieferbare Artikel zeigt,
sonst `"nur_lieferbar"`.
