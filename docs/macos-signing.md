# Signing & notarizing the macOS build

The macOS DMG is signed with an Apple Developer ID and notarized by Apple, so
it launches without the Gatekeeper "Apple cannot check it for malicious
software" warning. This document covers the one-time setup and the failure
modes you'll hit if something goes wrong.

For day-to-day building, just run:

```bash
export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
./scripts/build-macos.sh
```

## One-time setup

You only do this once per machine.

### 1. Apple Developer Program

Enroll at <https://developer.apple.com/programs/>. $99/year. Confirmation can
take a few hours.

### 2. Developer ID Application certificate

Easiest path is via Xcode (it manages the private key correctly so you don't
have to wrangle keychain imports):

1. Install Xcode from the App Store if you don't already have it.
2. Xcode → Settings → Accounts → `+` → sign in with your Apple ID.
3. Select your team → **Manage Certificates** → `+` → **Developer ID
   Application**.
4. Xcode generates the certificate, deposits both halves into your login
   keychain, and you're done.

Verify:

```bash
security find-identity -v -p codesigning
```

You should see one line containing `Developer ID Application: Your Name
(TEAMID1234)`. Copy that exact string — it's your `DEVELOPER_ID`.

### 3. App-specific password for notarization

`xcrun notarytool` authenticates with an app-specific password, not your
regular Apple ID password.

1. Visit <https://account.apple.com> → **Sign-In and Security** →
   **App-Specific Passwords** → **Generate**.
2. Label it something like `stowe-notary`.
3. Save the password somewhere secure — Apple will not show it again.

### 4. Store credentials in the keychain

```bash
xcrun notarytool store-credentials stowe-notary \
    --apple-id you@example.com \
    --team-id TEAMID1234 \
    --password xxxx-xxxx-xxxx-xxxx
```

This writes a profile called `stowe-notary` to your login keychain. The build
script references it by name, so the actual credentials never live in the
repo or in environment variables.

Smoke test:

```bash
xcrun notarytool history --keychain-profile stowe-notary
```

Should return an empty history without auth errors. If you hit
`HTTP 401`, the app-specific password is wrong; if you hit `HTTP 403`, the
team ID is wrong.

## Common failure modes

When notarization rejects a build, the only reliable debug source is the JSON
log:

```bash
xcrun notarytool log <submission-id> --keychain-profile stowe-notary
```

The `submission-id` is printed by `notarytool submit`.

| Symptom                                                                     | Cause                                                                                  | Fix                                                                                          |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `code object is not signed at all` during `codesign --verify`               | A nested file extension was missed by the find glob.                                   | Add the extension (or check for `.framework` bundles) in `scripts/build-macos.sh`.           |
| Notary log: `The executable does not have the hardened runtime enabled.`    | Forgot `--options runtime` on at least one binary.                                     | Make sure every `codesign` call in the build script passes `--options runtime`.              |
| Notary log: `The signature of the binary is invalid.`                       | Signed the outer `.app` before its inner binaries (Apple requires inside-out signing). | Re-run the deep-sign loop in the order the script defines: dylibs/sos → frameworks → MacOS/Stowe → outer .app. |
| App launches then immediately quits, Console shows `EXC_BAD_ACCESS (Code Signature Invalid)`. | Entitlements were not actually applied to the executable.                              | `codesign -d --entitlements :- /Applications/Stowe.app` should print the plist. If empty, re-sign. |
| `spctl: rejected source=Unnotarized Developer ID` after stapling.           | `xcrun stapler staple` failed silently, or ran on the wrong artifact.                  | `xcrun stapler validate` on both `.app` and `.dmg`. Re-staple if either fails.               |
| Notary "Accepted" but `stapler staple` says `Could not find base64 ticket`. | Apple's CDN hasn't propagated the ticket yet (rare).                                   | Wait 30 seconds and retry the staple.                                                        |

## Why this build script vs. PyInstaller's built-in signing?

`stowe.spec` deliberately leaves `codesign_identity=None`. PyInstaller's
built-in signing only signs the top-level `Stowe` Mach-O — every nested
`.dylib` and `.so` (Pillow, pydantic_core, ssl modules, etc.) goes
unsigned, and notarization rejects the bundle. The script signs everything
inside-out, which is what Apple's TN3147 recommends.
