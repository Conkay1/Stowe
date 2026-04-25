; Inno Setup script for Stowe (Windows)
; Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build:    iscc stowe.iss
; Input:    dist\Stowe\   (PyInstaller COLLECT output from stowe-windows.spec)
; Output:   Stowe-0.5.0-windows-setup.exe

#define AppName      "Stowe"
#define AppVersion   "0.5.0"
#define AppPublisher "Connor Kay"
#define AppURL       "https://stowe.health"
#define AppExeName   "Stowe.exe"

[Setup]
AppId={{A3F2C1D0-7E4B-4F9A-B8C5-2D6E1F0A3B7C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=Stowe-{#AppVersion}-windows-setup
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Bundle the entire PyInstaller one-folder output.
; Run: python -m PyInstaller stowe-windows.spec --noconfirm  before iscc.
Source: "dist\Stowe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";    Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Check for Edge WebView2 Runtime before installation proceeds.
// WebView2 ships with Windows 10 1803+ and Windows 11, so this only
// affects rare older systems.
function IsWebView2Installed(): Boolean;
var
  Ver: String;
begin
  Result :=
    RegQueryStringValue(
      HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', Ver) or
    RegQueryStringValue(
      HKCU,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', Ver);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWebView2Installed() then
  begin
    MsgBox(
      'Stowe requires the Microsoft Edge WebView2 Runtime.' + #13#10 +
      'It is pre-installed on Windows 10 (version 1803 or later) and Windows 11.' + #13#10#13#10 +
      'If the app does not open after installation, download WebView2 from:' + #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/',
      mbInformation, MB_OK);
  end;
end;
