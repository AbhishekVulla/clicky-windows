; Inno Setup script for Clicky Windows.
;
; Wraps the PyInstaller --onedir output (dist/Clicky/) into a single
; Setup.exe for GitHub Releases. Installs per user, so there is no admin
; prompt, which also lets it install on locked-down work machines.
;
; Build:
;     iscc installer\clicky.iss
;
; Output: installer\Output\Clicky-Windows-Setup.exe (about 165 MB after
; LZMA2 compression, including the optional local speech models' runtime).
;
; Inno Setup 6+ required: https://jrsoftware.org/isdl.php (free).

#define AppName "Clicky Windows"
#define AppVersion "0.5.0"
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
; Per-user install, no admin prompt. {userpf} = %LOCALAPPDATA%\Programs
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
; lowest never asks for elevation; auto would install system-wide for
; admins, which this app has no need for.
PrivilegesRequired=lowest
OutputDir=Output
; No version in the filename: GitHub serves /releases/latest/download/<name>,
; so a stable name keeps the README's download link working across releases.
; The version still shows on the release page, in the installer metadata and
; in Add or Remove Programs.
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
; Uninstall keeps the user's memory and knowledge-base folders. For a clean
; wipe, delete ~/.clicky-windows/ and ~/Documents/Clicky Wiki/ by hand.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
