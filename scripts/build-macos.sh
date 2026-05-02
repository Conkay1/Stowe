#!/usr/bin/env bash
# Build, sign, notarize, and staple Stowe.app + Stowe-<version>.dmg.
#
# Prerequisites (one-time, see docs/macos-signing.md):
#   - Apple Developer ID Application certificate in your login keychain.
#   - Notarytool keychain profile created via `xcrun notarytool store-credentials`.
#
# Usage:
#   export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
#   ./scripts/build-macos.sh
#
# Optional env:
#   KEYCHAIN_PROFILE    Notarytool profile name (default: stowe-notary)
#   PYTHON              Python interpreter (default: python3)

set -euo pipefail

: "${DEVELOPER_ID:?Set DEVELOPER_ID, e.g. 'Developer ID Application: Connor Kay (TEAMID)'}"
KEYCHAIN_PROFILE="${KEYCHAIN_PROFILE:-stowe-notary}"
PYTHON="${PYTHON:-python3}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Single source of truth for version: parse from stowe.spec.
VERSION=$(
    "$PYTHON" -c "import re, sys; m=re.search(r\"CFBundleShortVersionString['\\\"]\s*:\s*['\\\"]([^'\\\"]+)\", open('stowe.spec').read()); print(m.group(1) if m else sys.exit('Cannot find CFBundleShortVersionString in stowe.spec'))"
)

APP="dist/Stowe.app"
DMG_STAGING="dist/dmg-staging"
ENTITLEMENTS="assets/entitlements.plist"
DMG="Stowe-${VERSION}.dmg"
ZIP="Stowe-${VERSION}.zip"

echo "==> Stowe ${VERSION} — signing as: ${DEVELOPER_ID}"

echo "==> Clean previous build artifacts"
rm -rf build dist "$DMG" "$ZIP"

echo "==> PyInstaller build"
"$PYTHON" -m PyInstaller stowe.spec --noconfirm

if [[ ! -d "$APP" ]]; then
    echo "ERROR: $APP was not produced by PyInstaller." >&2
    exit 1
fi

echo "==> Deep-sign nested binaries (inside-out)"

# Every .dylib and .so PyInstaller bundled.
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 \
    | xargs -0 -I{} codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" "{}"

# Any nested .framework bundles (sign as a unit, not their internals).
if [[ -d "$APP/Contents/Frameworks" ]]; then
    find "$APP/Contents/Frameworks" -maxdepth 2 -type d -name "*.framework" -print0 \
        | xargs -0 -I{} codesign --force --options runtime --timestamp \
            --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" "{}"
fi

# Inner Mach-O executable (PyInstaller bootloader).
codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" \
    "$APP/Contents/MacOS/Stowe"

# Outer .app bundle last.
codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" "$APP"

echo "==> Verify signature"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign --display --entitlements :- "$APP" >/dev/null

echo "==> Zip .app for notarization"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Submit .app to Apple notary service (waits for ticket)"
xcrun notarytool submit "$ZIP" --keychain-profile "$KEYCHAIN_PROFILE" --wait

echo "==> Staple .app"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "==> Build DMG"
# Stage the signed .app alongside an /Applications symlink so the mounted
# DMG offers a one-step drag-to-install UX.
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"
hdiutil create -volname "Stowe" -srcfolder "$DMG_STAGING" \
    -ov -format UDZO "$DMG"

echo "==> Sign DMG"
codesign --force --sign "$DEVELOPER_ID" --timestamp "$DMG"

echo "==> Submit DMG to Apple notary service (waits for ticket)"
xcrun notarytool submit "$DMG" --keychain-profile "$KEYCHAIN_PROFILE" --wait

echo "==> Staple DMG"
xcrun stapler staple "$DMG"

echo "==> Gatekeeper assessment"
spctl --assess --type execute -vvv "$APP"
spctl --assess --type open --context context:primary-signature -vvv "$DMG"

# Tidy: the zip was only for notarization.
rm -f "$ZIP"

echo
echo "Done: $DMG"
