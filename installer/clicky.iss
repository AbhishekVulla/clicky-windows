; Inno Setup script for Clicky Windows.
;
; Wraps the PyInstaller --onedir output (dist/Clicky/) into a single
; Setup.exe for distribution via GitHub Releases.
;
; Pattern cribbed from doug-101/TreeLine v3.2.1 (PyQt6 + Inno Setup
; reference). Per-user install — no UAC prompt, lower friction for
; portfolio-tier users on locked-down corporate machines.
;
; Build:
;     iscc installer\clicky.iss
;
; Output: installer\Output\Clicky-Windows-Setup-v0.4.1.exe (~125-170 MB
; after Inno's LZMA2 compresses the onedir bundle, now with the full local stack).
;
; Inno Setup 6+ required: https://jrsoftware.org/isdl.php (free).

#define AppName "Clicky Windows"
#define AppVersion "0.4.2"
#define AppPublisher "Abhishek Vulla"
#define AppURL "https://github.com/AbhishekVulla/clicky-windows"
#define AppExeName "Clicky.exe"

[Setup]
AppId={{C9A8F1B3-7D2E-4A6F-9E8C-3B1D5F2A4C8D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
; Per-user install — no admin/UAC required. {userpf} = %LOCALAPPDATA%\Programs
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
; PrivilegesRequired=lowest avoids the elevation prompt; auto means
; per-user-or-system based on whether the user is admin (we want
; per-user always for portfolio scope).
PrivilegesRequired=lowest
OutputDir=Output
; Version-free filename ON PURPOSE (2026-07-21). GitHub serves
; /releases/latest/download/<asset-name>, so a stable name gives a permanent
; one-click download link for the README button, the YouTube description, and
; comment replies. A versioned name breaks that link on every release. The
; version is still on the release page, in the installer metadata, and in
; Add/Remove Programs, so nothing is actually lost.
OutputBaseFilename=Clicky-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
; Show "Run Clicky Windows" checkbox on final wizard page.
SetupLogging=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Bundle the entire PyInstaller --onedir output. recursesubdirs grabs
; the _internal/ tree (Qt plugins, Python stdlib, all bundled deps).
Source: "..\dist\Clicky\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Open Knowledge Folder"; Filename: "{userdocs}\Clicky Wiki"; Comment: "Drop per-app .md files here"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Don't delete user data on uninstall — preserve memory + KB folders.
; If a user wants a clean wipe, they delete ~/.clicky-windows/ +
; ~/Documents/Clicky Wiki/ manually. This matches the "transparency
; contract" UX: their data is theirs.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
