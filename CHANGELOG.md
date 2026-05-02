# Changelog

All notable changes to Stowe are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and the project follows
[Semantic Versioning](https://semver.org/).

## [0.6.0] — 2026-05-02

### Added
- **Custom categories.** Create your own expense categories alongside the
  seven built-in HSA defaults, manage them in Settings, and reassign existing
  expenses without losing data.
- **Spending analytics.** New page that breaks down your medical spending
  by category and over time.
- **Light, dark, and sepia themes.** Switchable from Settings; the choice
  persists across launches.
- **HSA account linking.** Track your HSA balance, contributions, and
  distributions inside Stowe.
- **Custodian CSV import.** Import distribution history from your HSA
  custodian and reconcile it against the Pulls you've recorded — quickly
  see which Pulls match a real distribution and which don't.
- **Signed and notarized macOS build.** The DMG is now signed with an Apple
  Developer ID and notarized by Apple. First launch no longer requires the
  right-click-Open Gatekeeper bypass. See `docs/macos-signing.md` for the
  build pipeline.
- `scripts/build-macos.sh` — one-shot script that builds the .app, deep-signs
  every nested binary, notarizes, staples, and produces the signed DMG.
- `assets/entitlements.plist` — hardened-runtime entitlements required by the
  PyInstaller + CPython + pywebview combination.

### Changed
- README and the stowe.health landing page updated to describe the new
  features and the signed-build install flow.
- "No account, no analytics" wording clarified to "No Stowe account, no
  cloud, no telemetry" — the new HSA-account-linking feature stores your
  account info locally only and is not a sign-up account.

## [0.5.0] — 2026

### Added
- **Backup-to-zip.** Download your database and receipts as a single zip
  file from inside the app for easy off-machine backup.
- **Windows packaging.** PyInstaller spec (`stowe-windows.spec`) and Inno
  Setup installer (`stowe.iss`) for `Stowe-x.y.z-windows-setup.exe`.

## [0.4.0] — 2026

### Added
- **Amount-first Pull flow.** Enter the dollar amount you pulled from your
  HSA; Stowe walks you through allocating it across receipts, with support
  for partial and multi-pull coverage.

## [0.3.0] — 2026

### Added
- **Pulls page.** Reimbursement pull events as a first-class concept, with
  their own page for review and editing.

## [0.2.0] — 2026

### Added
- **Native window** via pywebview/WKWebView when packaged.
- Brand refresh.
- stowe.health landing site.

### Fixed
- pywebview hidden imports and hook path for PyInstaller 6.x.

## [0.1.0] — 2026

Initial release.

[0.6.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.6.0
[0.5.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.5.0
[0.4.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.4.0
[0.3.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.3.0
[0.2.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.2.0
[0.1.0]: https://github.com/Conkay1/Stowe/releases/tag/v0.1.0
