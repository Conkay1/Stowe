# Stowe

**A personal, local-only tracker for your HSA receipts and expenses.**

[stowe.health](https://stowe.health) · [Releases](https://github.com/Conkay1/Stowe/releases) · MIT License

Stowe is a small desktop app for people who pay medical bills out-of-pocket with an HSA and want to defer reimbursement until later — possibly years later. The IRS lets you do this, but only if you can still produce the receipts when you pull the money out. That's what Stowe keeps track of.

Your data lives on your computer. Nothing you store is sent to a server. The app listens on localhost (127.0.0.1) only, and interface assets, including the chart library, are bundled with it. No Stowe account. No cloud. No telemetry.

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
- **Custom categories** — add your own alongside the built-in HSA categories
- **Spending analytics** — see where your medical spending is going, by category and over time
- **HSA account linking** — track HSA balance, contributions, and distributions in one place
- **Custodian CSV import** — import distribution history from your HSA custodian and reconcile it against the Pulls you've recorded
- **CSV export** — per-year or all-time, for your tax records or a spreadsheet
- Light, dark, and sepia themes — switchable in Settings

---

## Install

Stowe is currently available for macOS. You can also run it from source.

### macOS

1. Download [`Stowe-0.8.0.dmg`](https://github.com/Conkay1/Stowe/releases/download/v0.8.0/Stowe-0.8.0.dmg).
2. Open the DMG and drag **Stowe** into **Applications**.
3. Open **Stowe** from Applications. The build is signed with a Developer ID and notarized by Apple, so it launches normally — no Gatekeeper bypass needed.

### From source

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Conkay1/Stowe.git
cd Stowe
python3 run.py
```

`run.py` installs dependencies from PyPI when needed, creates the database on first run, finds a free port, and opens the app in your browser at `http://127.0.0.1`. Press `Ctrl+C` to stop. That pip step is the only network call on the from-source launch path. The server itself stays on loopback.

### Localhost only

Stowe listens on `127.0.0.1` only. Other devices on your network, including a phone on the same Wi-Fi, cannot open the app or its export API.

Versions before 0.8.0 accepted connections from other devices on the local network and loaded the chart library from a CDN. Update to 0.8.0.

---

## Where does my data live?

**Packaged app (macOS):**
- Database: `~/Library/Application Support/Stowe/database/stowe.db`
- Receipts: `~/Library/Application Support/Stowe/receipts/`

**From source:**
- Database: `./database/stowe.db` (inside the project folder)
- Receipts: `./receipts/`

Filenames on disk are random UUIDs, so nothing about the original filename leaks through `ls`.

**That's it.** No Stowe account. No cloud. No telemetry. To back up, copy those two paths. To migrate machines, copy them. To wipe everything, delete them.

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

Requires an Apple Developer ID and one-time keychain setup — see
[docs/macos-signing.md](docs/macos-signing.md).

```bash
export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
./scripts/build-macos.sh
```

Produces a signed, notarized, stapled `Stowe-<version>.dmg` in the repo root.

---

## Contributing

Issues and pull requests are welcome. This is a personal project that might stay small on purpose — "do one thing well" is the goal, not "become the Expensify of HSAs."

---

## Disclaimer

Stowe is a record-keeping tool, not tax advice. Consult IRS Publication 969 and a tax professional for what qualifies as a reimbursable medical expense.

---

## License

MIT — see [LICENSE](LICENSE).
