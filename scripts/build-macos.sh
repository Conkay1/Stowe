#!/usr/bin/env bash
# Build Stowe.app and Stowe-<version>.dmg.
#
# Signed builds (see docs/macos-signing.md):
#   Local: Developer ID Application certificate in the login keychain, and a
#   notarytool keychain profile (default name: stowe-notary).
#   CI:    MACOS_CERT_P12_BASE64 + MACOS_CERT_PASSWORD import the certificate
#   into a temporary keychain that is deleted on exit. Notarization uses an
#   App Store Connect API key, an Apple ID, or the keychain profile — in that
#   order.
#
# Usage:
#   export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
#   ./scripts/build-macos.sh
#
# Unsigned builds:
#   If DEVELOPER_ID is unset, the script warns and still produces an unsigned
#   .app and .dmg. It does not codesign, notarize, or staple. Older copies of
#   this script aborted instead; nothing in the repo depended on that abort.
#
# Optional env:
#   KEYCHAIN_PROFILE         Notarytool profile name (default: stowe-notary)
#   PYTHON                   Python interpreter (default: python3)
#   MACOS_CERT_P12_BASE64    Base64 Developer ID .p12 (CI)
#   MACOS_CERT_PASSWORD      Password for that .p12 (CI)
#   APPLE_API_KEY_P8_BASE64  Base64 App Store Connect API .p8 key
#   APPLE_API_KEY_PATH       Path to a .p8 key (used instead of the base64 var)
#   APPLE_API_KEY_ID         App Store Connect key ID
#   APPLE_API_ISSUER_ID      App Store Connect issuer ID
#   APPLE_ID                 Apple ID for notarization
#   APPLE_APP_PASSWORD       App-specific password
#   APPLE_TEAM_ID            Apple Developer team ID
#
# Do not run this script with xtrace (bash -x). It would print certificate
# passwords and API keys.

set -euo pipefail

KEYCHAIN_PROFILE="${KEYCHAIN_PROFILE:-stowe-notary}"
PYTHON="${PYTHON:-python3}"
DEVELOPER_ID="${DEVELOPER_ID:-}"

# Populated only for the CI certificate / API-key import. The EXIT trap
# restores the previous keychain search list and deletes the temp keychain.
SIGN_TMP=""
CI_KEYCHAIN=""
CI_KEYCHAIN_PASSWORD=""
CI_KEYCHAIN_CREATED=0
ORIG_DEFAULT_KEYCHAIN=""
ORIG_KEYCHAINS=()
NOTARY_ARGS=()
NOTARY_KEY_PATH=""
# Set once the notarization zip path is known. The EXIT trap removes it even
# when a later step (Gatekeeper assessment, for example) fails.
ZIP=""

cleanup_on_exit() {
    if [[ "${CI_KEYCHAIN_CREATED}" -eq 1 ]]; then
        if [[ -n "${ORIG_DEFAULT_KEYCHAIN}" ]]; then
            security default-keychain -s "${ORIG_DEFAULT_KEYCHAIN}" >/dev/null 2>&1 || true
        fi
        if [[ ${#ORIG_KEYCHAINS[@]} -gt 0 ]]; then
            security list-keychains -d user -s "${ORIG_KEYCHAINS[@]}" >/dev/null 2>&1 || true
        fi
        if [[ -n "${CI_KEYCHAIN}" ]]; then
            security delete-keychain "${CI_KEYCHAIN}" >/dev/null 2>&1 || true
        fi
    fi
    if [[ -n "${SIGN_TMP}" && -d "${SIGN_TMP}" ]]; then
        rm -rf "${SIGN_TMP}"
    fi
    # The zip exists only to upload the .app for notarization.
    if [[ -n "${ZIP}" ]]; then
        rm -f "${ZIP}" || true
    fi
}
trap cleanup_on_exit EXIT

ensure_sign_tmp() {
    if [[ -z "${SIGN_TMP}" ]]; then
        SIGN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/stowe-sign.XXXXXX")" || exit 1
    fi
}

# Write a base64 blob to a file. Accepts GNU (--decode / -d) and macOS (-D).
decode_base64_to_file() {
    local data="$1"
    local dest="$2"
    local stripped
    stripped="$(printf '%s' "${data}" | tr -d '[:space:]')"
    if [[ -z "${stripped}" ]]; then
        echo "ERROR: empty base64 value." >&2
        return 1
    fi
    if printf '%s' "${stripped}" | base64 --decode >"${dest}" 2>/dev/null && [[ -s "${dest}" ]]; then
        return 0
    fi
    if printf '%s' "${stripped}" | base64 -d >"${dest}" 2>/dev/null && [[ -s "${dest}" ]]; then
        return 0
    fi
    if printf '%s' "${stripped}" | base64 -D >"${dest}" 2>/dev/null && [[ -s "${dest}" ]]; then
        return 0
    fi
    echo "ERROR: failed to decode base64 payload." >&2
    return 1
}

trim_keychain_line() {
    local line="$1"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#\"}"
    line="${line%\"}"
    printf '%s' "${line}"
}

save_keychain_state() {
    local line trimmed
    ORIG_KEYCHAINS=()
    ORIG_DEFAULT_KEYCHAIN="$(security default-keychain 2>/dev/null || true)"
    ORIG_DEFAULT_KEYCHAIN="$(trim_keychain_line "${ORIG_DEFAULT_KEYCHAIN}")"
    while IFS= read -r line; do
        trimmed="$(trim_keychain_line "${line}")"
        if [[ -n "${trimmed}" ]]; then
            ORIG_KEYCHAINS+=("${trimmed}")
        fi
    done < <(security list-keychains -d user 2>/dev/null || true)
}

import_ci_certificate() {
    if ! command -v security >/dev/null 2>&1; then
        echo "ERROR: security(1) is required to import MACOS_CERT_P12_BASE64." >&2
        exit 1
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        echo "ERROR: openssl is required to create a temporary keychain password." >&2
        exit 1
    fi

    # Snapshot the search list before create-keychain, which may alter it.
    save_keychain_state
    ensure_sign_tmp

    local p12="${SIGN_TMP}/developer-id.p12"
    decode_base64_to_file "${MACOS_CERT_P12_BASE64}" "${p12}" || exit 1
    chmod 600 "${p12}"

    CI_KEYCHAIN="${SIGN_TMP}/stowe-ci.keychain-db"
    CI_KEYCHAIN_PASSWORD="$(openssl rand -hex 32)" || exit 1

    echo "==> Creating a temporary keychain and importing the Developer ID certificate"
    security create-keychain -p "${CI_KEYCHAIN_PASSWORD}" "${CI_KEYCHAIN}"
    CI_KEYCHAIN_CREATED=1
    # Stay unlocked for the rest of the job. The trap deletes the keychain.
    security set-keychain-settings -lut 21600 "${CI_KEYCHAIN}"
    security unlock-keychain -p "${CI_KEYCHAIN_PASSWORD}" "${CI_KEYCHAIN}"

    security import "${p12}" \
        -k "${CI_KEYCHAIN}" \
        -P "${MACOS_CERT_PASSWORD}" \
        -f pkcs12 \
        -A \
        -T /usr/bin/codesign \
        -T /usr/bin/security

    # Search list: temporary keychain first, then whatever the user already had.
    if [[ ${#ORIG_KEYCHAINS[@]} -gt 0 ]]; then
        security list-keychains -d user -s "${CI_KEYCHAIN}" "${ORIG_KEYCHAINS[@]}"
    else
        security list-keychains -d user -s "${CI_KEYCHAIN}"
    fi
    security default-keychain -s "${CI_KEYCHAIN}"

    # Partition list: let codesign use the private key with no UI prompt.
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
        -k "${CI_KEYCHAIN_PASSWORD}" "${CI_KEYCHAIN}" >/dev/null
}

# Set NOTARY_KEY_PATH to the .p8 file notarytool should use. A caller-supplied
# APPLE_API_KEY_PATH wins over APPLE_API_KEY_P8_BASE64 when both are set.
prepare_notary_key() {
    NOTARY_KEY_PATH=""
    if [[ -n "${APPLE_API_KEY_PATH:-}" ]]; then
        if [[ ! -f "${APPLE_API_KEY_PATH}" ]]; then
            echo "ERROR: APPLE_API_KEY_PATH does not exist." >&2
            exit 1
        fi
        NOTARY_KEY_PATH="${APPLE_API_KEY_PATH}"
        return 0
    fi
    ensure_sign_tmp
    local dest="${SIGN_TMP}/AuthKey.p8"
    decode_base64_to_file "${APPLE_API_KEY_P8_BASE64}" "${dest}" || exit 1
    chmod 600 "${dest}"
    NOTARY_KEY_PATH="${dest}"
}

api_key_env_present() {
    [[ -n "${APPLE_API_KEY_P8_BASE64:-}" || -n "${APPLE_API_KEY_PATH:-}" || -n "${APPLE_API_KEY_ID:-}" || -n "${APPLE_API_ISSUER_ID:-}" ]]
}

apple_id_env_present() {
    [[ -n "${APPLE_ID:-}" || -n "${APPLE_APP_PASSWORD:-}" || -n "${APPLE_TEAM_ID:-}" ]]
}

configure_notary_auth() {
    NOTARY_ARGS=()
    if api_key_env_present; then
        if [[ -z "${APPLE_API_KEY_ID:-}" || -z "${APPLE_API_ISSUER_ID:-}" ]]; then
            echo "ERROR: App Store Connect notarization requires APPLE_API_KEY_ID, APPLE_API_ISSUER_ID, and either APPLE_API_KEY_P8_BASE64 or APPLE_API_KEY_PATH." >&2
            exit 1
        fi
        if [[ -z "${APPLE_API_KEY_P8_BASE64:-}" && -z "${APPLE_API_KEY_PATH:-}" ]]; then
            echo "ERROR: App Store Connect notarization requires APPLE_API_KEY_ID, APPLE_API_ISSUER_ID, and either APPLE_API_KEY_P8_BASE64 or APPLE_API_KEY_PATH." >&2
            exit 1
        fi
        prepare_notary_key
        if [[ -z "${NOTARY_KEY_PATH}" ]]; then
            echo "ERROR: failed to prepare the App Store Connect API key." >&2
            exit 1
        fi
        NOTARY_ARGS=(--key "${NOTARY_KEY_PATH}" --key-id "${APPLE_API_KEY_ID}" --issuer "${APPLE_API_ISSUER_ID}")
        echo "==> Notarization auth: App Store Connect API key"
        return 0
    fi
    if apple_id_env_present; then
        if [[ -z "${APPLE_ID:-}" || -z "${APPLE_APP_PASSWORD:-}" || -z "${APPLE_TEAM_ID:-}" ]]; then
            echo "ERROR: Apple ID notarization requires APPLE_ID, APPLE_APP_PASSWORD, and APPLE_TEAM_ID." >&2
            exit 1
        fi
        NOTARY_ARGS=(--apple-id "${APPLE_ID}" --password "${APPLE_APP_PASSWORD}" --team-id "${APPLE_TEAM_ID}")
        echo "==> Notarization auth: Apple ID"
        return 0
    fi
    NOTARY_ARGS=(--keychain-profile "${KEYCHAIN_PROFILE}")
    echo "==> Notarization auth: keychain profile ${KEYCHAIN_PROFILE}"
}

notary_submit() {
    local artifact="$1"
    if [[ ${#NOTARY_ARGS[@]} -eq 0 ]]; then
        echo "ERROR: notarization auth was not configured." >&2
        exit 1
    fi
    xcrun notarytool submit "${artifact}" "${NOTARY_ARGS[@]}" --wait
}

sign_nested_binaries() {
    echo "==> Deep-sign nested binaries (inside-out)"
    echo "    .dylib / .so / .framework: hardened runtime + timestamp, no entitlements"
    echo "    Contents/MacOS/Stowe and the .app bundle: entitlements applied"

    # Nested libraries must not carry the app entitlements. Notarization
    # rejects library-inappropriate keys; the hardened runtime enforces
    # entitlements on the main executable.
    find "${APP}/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 \
        | xargs -0 -I{} codesign --force --options runtime --timestamp \
            --sign "${DEVELOPER_ID}" "{}"

    if [[ -d "${APP}/Contents/Frameworks" ]]; then
        find "${APP}/Contents/Frameworks" -maxdepth 2 -type d -name "*.framework" -print0 \
            | xargs -0 -I{} codesign --force --options runtime --timestamp \
                --sign "${DEVELOPER_ID}" "{}"
    fi

    codesign --force --options runtime --timestamp \
        --entitlements "${ENTITLEMENTS}" --sign "${DEVELOPER_ID}" \
        "${APP}/Contents/MacOS/Stowe"

    codesign --force --options runtime --timestamp \
        --entitlements "${ENTITLEMENTS}" --sign "${DEVELOPER_ID}" "${APP}"
}

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

SIGNING=0
if [[ -z "${DEVELOPER_ID}" ]]; then
    echo "WARNING: DEVELOPER_ID is not set." >&2
    echo "         Building an unsigned .app and .dmg. Codesign, notarization, and stapling are skipped." >&2
    echo "         Older versions of this script exited here. Unsigned builds are now intentional;" >&2
    echo "         see docs/macos-signing.md." >&2
    if [[ -n "${MACOS_CERT_P12_BASE64:-}" || -n "${MACOS_CERT_PASSWORD:-}" ]]; then
        echo "WARNING: MACOS_CERT_P12_BASE64 / MACOS_CERT_PASSWORD are set, but signing still requires DEVELOPER_ID." >&2
    fi
    echo "==> Stowe ${VERSION} — unsigned build"
else
    SIGNING=1
    if ! command -v codesign >/dev/null 2>&1; then
        echo "ERROR: codesign is required when DEVELOPER_ID is set." >&2
        exit 1
    fi
    if [[ -n "${MACOS_CERT_P12_BASE64:-}" || -n "${MACOS_CERT_PASSWORD:-}" ]]; then
        if [[ -z "${MACOS_CERT_P12_BASE64:-}" || -z "${MACOS_CERT_PASSWORD:-}" ]]; then
            echo "ERROR: MACOS_CERT_P12_BASE64 and MACOS_CERT_PASSWORD must both be set." >&2
            exit 1
        fi
        import_ci_certificate
    fi
    configure_notary_auth
    echo "==> Stowe ${VERSION} — signing as: ${DEVELOPER_ID}"
fi

echo "==> Clean previous build artifacts"
rm -rf build dist "$DMG" "$ZIP"

echo "==> PyInstaller build"
"$PYTHON" -m PyInstaller stowe.spec --noconfirm

if [[ ! -d "$APP" ]]; then
    echo "ERROR: $APP was not produced by PyInstaller." >&2
    exit 1
fi

if [[ "${SIGNING}" -eq 1 ]]; then
    sign_nested_binaries

    echo "==> Verify signature"
    codesign --verify --deep --strict --verbose=2 "$APP"
    codesign --display --entitlements :- "$APP" >/dev/null

    echo "==> Zip .app for notarization"
    ditto -c -k --keepParent "$APP" "$ZIP"

    echo "==> Submit .app to Apple notary service (waits for ticket)"
    notary_submit "$ZIP"

    echo "==> Staple .app"
    xcrun stapler staple "$APP"
    xcrun stapler validate "$APP"
fi

echo "==> Build DMG"
# Stage the signed (or unsigned) .app alongside an /Applications symlink so
# the mounted DMG offers a one-step drag-to-install UX.
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"
hdiutil create -volname "Stowe" -srcfolder "$DMG_STAGING" \
    -ov -format UDZO "$DMG"

if [[ "${SIGNING}" -eq 1 ]]; then
    echo "==> Sign DMG"
    codesign --force --sign "$DEVELOPER_ID" --timestamp "$DMG"

    echo "==> Submit DMG to Apple notary service (waits for ticket)"
    notary_submit "$DMG"

    echo "==> Staple DMG"
    xcrun stapler staple "$DMG"

    echo "==> Gatekeeper assessment"
    # spctl lives in /usr/sbin, which is not on PATH for a daemon launchd job.
    # codesign, ditto, hdiutil, security, and xcrun are in /usr/bin (stapler is
    # reached through xcrun), so those bare names still resolve.
    /usr/sbin/spctl --assess --type execute -vvv "$APP"
    /usr/sbin/spctl --assess --type open --context context:primary-signature -vvv "$DMG"
fi

echo
echo "Done: $DMG"
