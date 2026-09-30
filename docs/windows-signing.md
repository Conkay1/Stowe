# Signing the Windows build

`scripts/build-windows.ps1` produces `Stowe-<version>-windows-setup.exe`.
Signing is optional. If `WINDOWS_CERT_PFX_BASE64` and
`WINDOWS_CERT_PASSWORD` are both unset, the script says the build will be
unsigned and leaves `dist\Stowe\Stowe.exe` and the setup exe unsigned.

The macOS flow is documented in [macos-signing.md](macos-signing.md).

## What gets signed

Signing is done in the PowerShell script, not in `stowe.iss`. Inno Setup
copies `dist\Stowe\` into the installer, so the payload has to be signed
first or the installed `Stowe.exe` would be unsigned.

1. After PyInstaller, if a certificate is configured, `signtool` signs
   `dist\Stowe\Stowe.exe`.
2. Inno Setup (`iscc stowe.iss`) builds the setup exe.
3. `signtool` signs `Stowe-<version>-windows-setup.exe`.

The flags are `/fd SHA256 /tr <timestamp URL> /td SHA256`. The timestamp
URL defaults to `http://timestamp.digicert.com` and can be overridden with
`WINDOWS_TIMESTAMP_URL`.

`signtool.exe` is taken from the newest Windows SDK under
`Windows Kits\10\bin\<version>\x64\`, then any other `signtool.exe` in that
tree, then `PATH`. The script errors if signing was requested and
`signtool` cannot be found.

The `.pfx` is decoded to a temp file for the two `signtool` invocations and
deleted when the script exits, including when the build fails.

## CI secrets

[`.github/workflows/release-build.yml`](../.github/workflows/release-build.yml)
maps these repository secrets into the Windows job. The workflow is
`workflow_dispatch` only and uploads the setup exe as an artifact. It does
not publish a GitHub Release.

Add the secrets under **Settings → Secrets and variables → Actions**. Do
not commit them.

| Secret | Required to sign | Purpose |
| --- | --- | --- |
| `WINDOWS_CERT_PFX_BASE64` | Yes | Base64 of the Authenticode `.pfx` (certificate plus private key). Whitespace is ignored. |
| `WINDOWS_CERT_PASSWORD` | Yes | Password for that `.pfx`. |
| `WINDOWS_TIMESTAMP_URL` | No | RFC3161 timestamp server. Default `http://timestamp.digicert.com` when unset or empty. |

Both certificate secrets must be set together. Setting only one is an
error. Setting neither produces an unsigned installer.

Encode the `.pfx` without writing it into the repo:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("codesign.pfx"))
```

```bash
# Linux
base64 -w 0 codesign.pfx

# macOS
base64 -i codesign.pfx
```

Paste the output into `WINDOWS_CERT_PFX_BASE64`.

## Local signed build

```powershell
$env:WINDOWS_CERT_PFX_BASE64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("codesign.pfx"))
$env:WINDOWS_CERT_PASSWORD = "<pfx-password>"
# optional:
$env:WINDOWS_TIMESTAMP_URL = "http://timestamp.digicert.com"
.\scripts\build-windows.ps1
```

Clear those variables (or open a new shell) before a build you want left
unsigned.
