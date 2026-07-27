#ifndef MyAppVersion
  #error MyAppVersion must be supplied by ISCC
#endif

[Setup]
AppId={{C174E242-E193-5863-8A46-F16152875173}
AppName=AACC
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
AppPublisher=AACC
AppPublisherURL=https://github.com/zhangboqian2022/AI-Agent-Control-Center
DefaultDirName={localappdata}\Programs\AACC
DefaultGroupName=AACC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
UsePreviousAppDir=yes
UninstallLogMode=append
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
OutputDir=..\dist\installer
OutputBaseFilename=AACC-{#MyAppVersion}-Setup
UninstallFilesDir={app}\uninstall
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 / Create a desktop shortcut"; GroupDescription: "其他选项 / Additional options:"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\AACC\AACC.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\aacc-spawn.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AACC.exe"; Description: "启动 AACC / Launch AACC"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[Code]
function ShutdownExistingAACC(var ErrorMessage: String): Boolean;
var
  AACCPath: String;
  ResultCode: Integer;
begin
  Result := True;
  ErrorMessage := '';
  AACCPath := ExpandConstant('{app}\AACC.exe');
  if not FileExists(ExpandConstant('{app}\AACC.exe')) then
    Exit;

  if (not Exec(
    AACCPath,
    '--shutdown-for-update',
    ExpandConstant('{app}'),
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  )) or (ResultCode <> 0) then
  begin
    ErrorMessage :=
      'AACC 无法安全退出。请从系统托盘退出 AACC，然后重试。' + #13#10 +
      'AACC could not exit safely. Exit AACC from the system tray and retry.';
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  if ShutdownExistingAACC(Result) then
    Result := '';
end;

function InitializeUninstall: Boolean;
var
  ErrorMessage: String;
begin
  Result := ShutdownExistingAACC(ErrorMessage);
  if not Result then
    SuppressibleMsgBox(ErrorMessage, mbError, MB_OK, IDOK);
end;
