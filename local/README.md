# Apple-Check lokal (Windows)

Apple blockt GitHub-Actions-IPs (HTTP 541). Deshalb läuft die Apple-Store-Abfrage auf einem Windows-Rechner im Heimnetz.

Prüft alle 5 Minuten iPhone Duo 256 GB Sternenweiß (MK2D4ZD/A) in München (Rosenstraße, OEZ), Augsburg und Sindelfingen.
Sofort-Alarm bei Bestand, Tagesmeldung mit Restock-Zeiten ab 21:00, Warnung nach 6 Fehlläufen in Folge.

## Einrichtung

1. Ordner `local` auf den Rechner kopieren, z. B. nach `C:\Users\<Name>\iphone-check`.
2. `config.example.json` als `config.json` kopieren und Bot-Token sowie Chat-ID eintragen.
3. Test: `py apple_check.py --test` – zeigt die vier Stores und sendet eine Telegram-Testnachricht.
4. Aufgabe anlegen: `powershell -ExecutionPolicy Bypass -File install_task.ps1`

Rechner darf gesperrt sein, aber nicht in den Ruhezustand gehen.

## Entfernen

`Unregister-ScheduledTask -TaskName "iPhone Duo Apple-Check" -Confirm:$false`
