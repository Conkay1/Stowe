# Stowe

**A personal, local-only tracker for your HSA receipts and expenses.**

[stowe.health](https://stowe.health) · [Releases](https://github.com/Conkay1/Stowe/releases) · MIT License

Stowe is a small desktop app for people who pay medical bills out-of-pocket with an HSA and want to defer reimbursement until later — possibly years later. The IRS lets you do this, but only if you can still produce the receipts when you pull the money out. That's what Stowe keeps track of.

Your data lives on your computer. Nothing is sent to a server. No account, no sync, no telemetry.

---

## Why this exists

The standard HSA-optimization move is to pay medical expenses from a checking account, let the HSA grow tax-free, and reimburse yourself years or decades later. To do it safely you need receipts — every single one — matched to every expense, for as long as you might want to withdraw.

Existing apps in this space are SaaS products that ask you to upload medical receipts to someone else's servers. Stowe is the opposite: a single-file SQLite database and a folder of receipts, both living on your machine, with a clean local web UI on top.

---

## Features

- Log expenses with merchant, date, amount, category, and notes
- Attach one or more receipts per expense (JPG, PNG, WebP, HEIC, PDF — 10 MB each)
- Mark expenses reimbursed when you pull the money out; the reimbursement date is recorded automatically
- **Vault Balance** — running total of unreimbursed, receipt-backed expenses you could claim today
- **Annual Ledger** — year-by-year breakdown with receipt-coverage percentage
- **CSV export** — per-year or all-time, for your tax records or a spreadsheet
- Dark-mode UI, responsive on mobile when accessed over your local network

---

## Install

### macOS (recommended)

1. Download `Stowe-0.4.0.dmg` from the latest [Release](https://github.com/Conkay1/Stowe/releases).
2. Open the DMG and drag **Stowe** into **Applications**.
3. **First launch:** because this build is unsigned, macOS will say *"Stowe can't be opened because Apple cannot check it for malicious software."* This is expected. Bypass it once:
   - **Option A:** Right-click `Stowe.app` → **Open** → confirm **Open** in the dialog.
   - **Option B:** Open **System Settings → Privacy & Security**, scroll to the "Stowe was blocked" message, click **Open Anyway**.
   Subsequent launches work normally.

Windows installer is on the way.

### From source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Conkay1/Stowe.git
cd Stowe
python3 run.py
```

`run.py` installs dependencies, creates the database on first run, finds a free port, and opens the app in your browser. Press `Ctrl+C` to stop.

### Access from your phone on the same WiFi

`run.py` prints a LAN URL on startup (e.g. `http://192.168.1.42:8000`). Open that on your phone's browser to snap receipts with your camera. On iOS, tap the Share button and "Add to Home Screen" to install it as a PWA.

---

## Where does my data live?

**Packaged app (macOS):**
- Database: `~/Library/Application Support/Stowe/database/stowe.db`
- Receipts: `~/Library/Application Support/Stowe/receipts/`

**From source:**
- Database: `./database/stowe.db` (inside the project folder)
- Receipts: `./receipts/`

Filenames on disk are random UUIDs, so nothing about the original filename leaks through `ls`.

**That's it.** No cloud, no account, no analytics. To back up, copy those two paths. To migrate machines, copy them. To wipe everything, delete them.

If you want off-machine backups, drop the data dir inside iCloud Drive, Dropbox, or similar. The DB is a single SQLite file; receipts are opaque blobs. Both back up cleanly.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Tech stack

- FastAPI (Python 3.10+)
- SQLite via SQLAlchemy 2.x
- Vanilla JavaScript frontend (no build step, no npm)
- Uvicorn ASGI server
- pywebview — native WKWebView window when packaged
- PWA-ready (manifest + service worker for offline cache)

---

## Building the macOS app

```bash
pip install pyinstaller pywebview
python -m PyInstaller stowe.spec --noconfirm
# .app lands in dist/Stowe/
```

To package as a DMG:

```bash
hdiutil create -volname "Stowe" -srcfolder "dist/Stowe" \
  -ov -format UDZO "Stowe-0.4.0.dmg"
```

---

## Contributing

Issues and pull requests are welcome. This is a personal project that might stay small on purpose — "do one thing well" is the goal, not "become the Expensify of HSAs."

---

## Disclaimer

Stowe is a record-keeping tool, not tax advice. Consult IRS Publication 969 and a tax professional for what qualifies as a reimbursable medical expense.

---

## License

MIT — see [LICENSE](LICENSE).
