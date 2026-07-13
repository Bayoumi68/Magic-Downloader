; Inno Setup script for Magic Downloader
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" MagicDownloader.iss
; Requires: the PyInstaller build first (dist\MagicDownloader\).
; Output:  installer\MagicDownloader-Setup-<version>.exe

#define MyAppName "Magic Downloader"
#define MyAppVersion "0.5.3"
#define MyAppPublisher "Magic Downloader"
#define MyAppExeName "MagicDownloader.exe"

[Setup]
; A stable, unique ID for upgrades/uninstall. Keep this the same across versions.
AppId={{A1E9F3C2-6B4D-4E8A-9C21-7F5D3B8A1E90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Always show the "Select Destination Location" page, even when upgrading.
DisableDirPage=no
UsePreviousAppDir=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Close a running Magic Downloader before installing so the .exe can be
; replaced (otherwise an upgrade over a running/tray instance keeps the old
; files → "installed the old version").
CloseApplications=yes
RestartApplications=no
; Install per-user by default (no admin prompt); user can pick all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=installer
; Version-less filename so the GitHub "latest" download link is permanent.
OutputBaseFilename=MagicDownloader-Setup
SetupIconFile=browser_extension\icons\app.ico
LicenseFile=LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller onedir output (exe + _internal with all dependencies).
Source: "dist\MagicDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Handy references alongside the app.
Source: "INSTALL_BROWSER.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "FIREFOX_INSTALL.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
