# Lokale Checks (Windows)

## eBay-Check (aktiv)

eBay blockt GitHub-Actions-IPs (HTTP 403). Deshalb läuft die eBay-Suche (Sofort-Kaufen und Auktionen < 24 h) auf einem Windows-Rechner im Heimnetz. `ebay_check.py` nutzt `check_ebay()` aus `../tracker.py` – Filter und Telegram-Meldungen sind identisch.

### Einrichtung

1. Repo-ZIP entpacken, sodass `tracker.py` direkt über dem Ordner `local` liegt.
2. `config.example.json` als `config.json` kopieren, Bot-Token und Chat-ID eintragen.
3. Bibliotheken: `py -m pip install requests beautifulsoup4`
4. Test: `py ebay_check.py` – Ausgabe zeigt HTTP-Status und Anzahl der eBay-Einträge.
5. Aufgabe anlegen: `powershell -ExecutionPolicy Bypass -File install_task.ps1`

Rechner darf gesperrt sein, aber nicht in den Ruhezustand gehen.

Entfernen: `Unregister-ScheduledTask -TaskName "iPhone Duo eBay-Check" -Confirm:$false`

## Apple-Check (Reserve)

Der Apple-Check läuft über GitHub Actions (`/shop/retail/pickup-message`). `apple_check.py` ist nur Reserve, falls Apple GitHub-IPs künftig blockt – dann in `tracker.py` deaktivieren und lokal einplanen. Nie beide parallel betreiben (doppelte Alarme).
