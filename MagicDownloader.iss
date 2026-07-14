; Inno Setup script for Magic Downloader
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" MagicDownloader.iss
; Requires: the PyInstaller build first (dist\MagicDownloader\).
; Output:  installer\MagicDownloader-Setup-<version>.exe

#define MyAppName "Magic Downloader"
#define MyAppVersion "0.5.5"
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
; DON'T use Inno's Restart Manager: it asks the app to close gracefully, which
; Magic Downloader treats as "hide to tray" (it never exits) — so the RM step
; just shows "unable to close application". Instead we hard-kill the running
; instance ourselves from [Code] (taskkill /F), before any files are touched.
CloseApplications=no
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

[Code]
// Magic Downloader keeps running in the system tray when its window is closed
// (IDM-style), so an upgrade finds MagicDownloader.exe + its _internal files
// locked by that background process. A graceful close just hides it to tray,
// so we FORCE-terminate it (taskkill /F, which bypasses the tray behaviour)
// as early as possible — before any wizard page and before any file is
// touched — guaranteeing the new version installs cleanly.
procedure KillRunningApp;
var
  ResultCode: Integer;
begin
  // NOTE: no /T. The user often launches this installer FROM the app (opened
  // from the download list), making Setup a CHILD of MagicDownloader.exe — and
  // /T would kill the whole tree, taking the installer down with it. Killing
  // just the app by image name unlocks its files; the installer (a differently
  // named child) keeps running.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExeName}',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  KillRunningApp;   // hard-close any running/tray instance up front
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp;   // ...and again right before files are written
  Sleep(600);       // let Windows release the file handles
  Result := '';
end;
