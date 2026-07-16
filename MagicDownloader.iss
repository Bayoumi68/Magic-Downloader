; Inno Setup script for Magic Downloader
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" MagicDownloader.iss
; Requires: the PyInstaller build first (dist\MagicDownloader\).
; Output:  installer\MagicDownloader-Setup-<version>.exe

#define MyAppName "Magic Downloader"
#define MyAppVersion "0.5.25"
#define MyAppPublisher "Magic Downloader"
#define MyAppExeName "MagicDownloader.exe"
; A stable, unique ID for upgrades/uninstall. Keep this the same across versions.
; Everything that identifies "our" installation keys off this — never off the
; display name (other vendors ship products called "Magic Downloader" too).
#define MyAppId "{A1E9F3C2-6B4D-4E8A-9C21-7F5D3B8A1E90}"

[Setup]
AppId={{#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Show the "Select Destination Location" page, but default it to the EXISTING
; install folder so an upgrade overwrites the current copy in place instead of
; creating a second install in a different folder (UsePreviousAppDir=no did that).
DisableDirPage=no
UsePreviousAppDir=yes
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
; restartreplace/uninsrestartdelete: if a file (e.g. a loaded VCRUNTIME140.dll)
; can't be replaced during an upgrade because it's still in use, queue it for
; replacement on the next reboot instead of failing with "DeleteFile failed".
Source: "dist\MagicDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion restartreplace uninsrestartdelete
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
const
  // Inno registers every install under <hive>\...\Uninstall\<AppId>_is1. We look
  // ONLY for our own AppId — matching on the display name would find unrelated
  // products that happen to share the name and uninstall someone else's app.
  OurUninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';

type
  TPrevInstall = record
    Found: Boolean;
    Root: Integer;
    UninstExe: String;
    Location: String;
    Version: String;
  end;

// Magic Downloader keeps running in the system tray when its window is closed
//, so an upgrade finds MagicDownloader.exe + its _internal files
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

// Read whatever installation of ours is registered in one hive.
function ReadPrevInstall(Root: Integer): TPrevInstall;
var
  S: String;
begin
  Result.Found := False;
  Result.Root := Root;
  Result.UninstExe := '';
  Result.Location := '';
  Result.Version := '';
  if RegQueryStringValue(Root, OurUninstKey, 'UninstallString', S) then
  begin
    Result.Found := True;
    Result.UninstExe := RemoveQuotes(S);
    RegQueryStringValue(Root, OurUninstKey, 'InstallLocation', Result.Location);
    RegQueryStringValue(Root, OurUninstKey, 'DisplayVersion', Result.Version);
  end;
end;

function SamePath(A, B: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(Trim(A)),
                        RemoveBackslashUnlessRoot(Trim(B))) = 0;
end;

// Enforce ONE installation on the machine.
//
// Setup can install per-user (HKCU -> {localappdata}\Programs) or for all users
// (HKLM -> {commonpf}), and UsePreviousAppDir only consults the CURRENT mode's
// hive. So switching modes used to leave the previous copy installed in the
// other location, with its own uninstall entry and shortcuts — two Magic
// Downloaders on one system. Now the older copy is uninstalled first.
procedure RemoveOtherInstall(P: TPrevInstall; TargetDir: String);
var
  ResultCode, Waited: Integer;
  Dummy: String;
begin
  if not P.Found then
    Exit;
  // Same folder = an ordinary in-place upgrade; Inno replaces it correctly.
  if (P.Location <> '') and SamePath(P.Location, TargetDir) then
    Exit;

  if FileExists(P.UninstExe) then
  begin
    if not WizardSilent then
      if MsgBox('Another copy of Magic Downloader is installed here:' + #13#10#13#10
                + '    ' + P.Location + '  (version ' + P.Version + ')' + #13#10#13#10
                + 'Only one copy should be installed. Remove that one and keep '
                + 'this new installation in:' + #13#10#13#10
                + '    ' + TargetDir + ' ?',
                mbConfirmation, MB_YESNO) = IDNO then
        Exit;
    KillRunningApp;
    // The uninstaller relaunches itself from a temp copy, so the process we
    // start exits immediately — waiting on Exec proves nothing. Poll the
    // registry key until it's actually gone (cap the wait; never hang Setup).
    Exec(P.UninstExe, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Waited := 0;
    while (Waited < 60000)
      and RegQueryStringValue(P.Root, OurUninstKey, 'UninstallString', Dummy) do
    begin
      Sleep(500);
      Waited := Waited + 500;
    end;
  end;

  // A half-finished uninstall (or an install whose folder was deleted by hand)
  // leaves the key behind, so Windows keeps listing a program that isn't there.
  // Drop our own stale key — this only ever touches OurUninstKey.
  if RegKeyExists(P.Root, OurUninstKey) then
    RegDeleteKeyIncludingSubkeys(P.Root, OurUninstKey);
end;

function InitializeSetup(): Boolean;
begin
  KillRunningApp;   // hard-close any running/tray instance up front
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
begin
  KillRunningApp;   // ...and again right before files are written
  Target := ExpandConstant('{app}');
  // Both hives: whichever one isn't where we're installing gets cleaned out.
  RemoveOtherInstall(ReadPrevInstall(HKEY_CURRENT_USER), Target);
  RemoveOtherInstall(ReadPrevInstall(HKEY_LOCAL_MACHINE), Target);
  KillRunningApp;   // the uninstaller may have restarted it
  Sleep(600);       // let Windows release the file handles
  Result := '';
end;

// Final registry alignment: after we've installed, our AppId must be registered
// exactly once. Anything left pointing at an uninstaller that no longer exists
// is a phantom entry in Programs & Features — remove it.
procedure DropOrphanKey(Root: Integer);
var
  P: TPrevInstall;
begin
  P := ReadPrevInstall(Root);
  if P.Found and (not FileExists(P.UninstExe)) then
    RegDeleteKeyIncludingSubkeys(Root, OurUninstKey);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    DropOrphanKey(HKEY_CURRENT_USER);
    DropOrphanKey(HKEY_LOCAL_MACHINE);
  end;
end;
