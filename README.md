# Habanos-Wächter

Prüft jede halbe Stunde (jeweils um :26 und :56, Takt über cron-job.org) den Habanos-Bestand bei

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

und schickt dir eine Push-Nachricht aufs Handy, wenn

- eine Zigarre **wieder verfügbar** ist, oder
- ein **neues Produkt** auftaucht (bei Zigarrentraum auch, wenn es noch nicht lieferbar ist).

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
- **Zu viele Änderungen auf einmal** (z. B. großer Wareneingang): die ersten 8 kommen einzeln,
  der Rest als eine Sammelnachricht.
- **Aktuellen Bestand ansehen:** `state.json` im Repo öffnen, oder lokal `python waechter.py --status`.
- **GitHub pausiert geplante Workflows** in öffentlichen Repos nach 60 Tagen ohne Aktivität.
  Der Wächter speichert täglich einen kleinen Zeitstempel, das sollte reichen. Falls doch
  pausiert: unter **Actions** einfach wieder aktivieren.
- **Taktgeber:** GitHub startet geplante Läufe bei kostenlosen Projekten nur alle 2–6 Stunden.
  Den 30-Minuten-Takt liefert deshalb **cron-job.org**: Ein Cronjob ruft um :26 und :56 per
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

Belastbare Muster brauchen Zeit: erste Hinweise nach ein paar Wochen, verlässlich nach
2–3 Monaten. Zeitpunkte sind auf etwa 30 Minuten genau (Prüfintervall).

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

## Einen weiteren Shop hinzufügen

In `waechter.py` eine `parse_…`- und eine `fetch_…`-Funktion schreiben und den Shop in die
Liste `SHOPS` eintragen. Modus `"voll"`, wenn die Seite auch nicht lieferbare Artikel zeigt,
sonst `"nur_lieferbar"`.
