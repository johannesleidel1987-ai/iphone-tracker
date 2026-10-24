name: iPhone Duo 256GB Tracker

on:
  schedule:
    - cron: '*/20 * * * *' # Prüft automatisch alle 20 Minuten
  workflow_dispatch:       # Erlaubt manuellen Test-Start per Klick

jobs:
  track:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Code auschecken
        uses: actions/checkout@v4

      - name: Python einrichten
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Bibliotheken installieren
        run: pip install requests beautifulsoup4

      - name: Tracker ausführen
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python tracker.py

      - name: Gesehene Angebote speichern (verhindert doppelte Alarme)
        run: |
          git config user.name "TrackerBot"
          git config user.email "bot@example.com"
          git add seen_offers.json || true
          git diff --staged --quiet || git commit -m "Update seen offers"
          git push
