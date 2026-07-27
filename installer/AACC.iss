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
MinVersion=10.0.17763
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

[Files]
Source: "..\dist\AACC\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\AACC\AACC.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\aacc-spawn.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\installer\internal-manifest-v1.txt"; DestDir: "{app}\uninstall"; Flags: ignoreversion
Source: "shutdown-v1.capability"; DestDir: "{app}\uninstall"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AACC.exe"; Description: "启动 AACC / Launch AACC"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[Code]
const
  AACCWindowTitle = 'AI Agent Control Center';
  ShutdownCapabilityName = 'shutdown-v1.capability';
  ShutdownControlTimeoutMilliseconds = 25000;
  WAIT_OBJECT_0 = 0;
  WAIT_TIMEOUT = 258;
  STARTF_USESHOWWINDOW = 1;
  FILE_ATTRIBUTE_REPARSE_POINT = $400;

type
  { These pointer-sized records are reviewed for the pinned Inno Setup 6.7.1
    x86 compiler. Re-review their ABI before an Inno 7 or 64-bit compiler move. }
  TStartupInfo = record
    cb: DWORD;
    lpReserved: String;
    lpDesktop: String;
    lpTitle: String;
    dwX: DWORD;
    dwY: DWORD;
    dwXSize: DWORD;
    dwYSize: DWORD;
    dwXCountChars: DWORD;
    dwYCountChars: DWORD;
    dwFillAttribute: DWORD;
    dwFlags: DWORD;
    wShowWindow: Word;
    cbReserved2: Word;
    lpReserved2: LongWord;
    hStdInput: THandle;
    hStdOutput: THandle;
    hStdError: THandle;
  end;

  TProcessInformation = record
    hProcess: THandle;
    hThread: THandle;
    dwProcessId: DWORD;
    dwThreadId: DWORD;
  end;

function CreateProcess(
  lpApplicationName: String;
  lpCommandLine: String;
  lpProcessAttributes: LongWord;
  lpThreadAttributes: LongWord;
  bInheritHandles: BOOL;
  dwCreationFlags: DWORD;
  lpEnvironment: LongWord;
  lpCurrentDirectory: String;
  const lpStartupInfo: TStartupInfo;
  var lpProcessInformation: TProcessInformation
): BOOL;
  external 'CreateProcessW@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function GetExitCodeProcess(hProcess: THandle; var lpExitCode: DWORD): BOOL;
  external 'GetExitCodeProcess@kernel32.dll stdcall';
function TerminateProcess(hProcess: THandle; uExitCode: DWORD): BOOL;
  external 'TerminateProcess@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function ShutdownFailureMessage: String;
begin
  Result :=
    'AACC 无法安全退出。请从系统托盘退出 AACC，然后重试。' + #13#10 +
    'AACC could not exit safely. Exit AACC from the system tray and retry.';
end;

function RunManagedShutdownControl(
  AACCPath: String;
  var ResultCode: Integer
): Boolean;
var
  CommandLine: String;
  StartupInfo: TStartupInfo;
  ProcessInfo: TProcessInformation;
  WaitResult: DWORD;
  ExitCode: DWORD;
begin
  Result := False;
  ResultCode := -1;
  CommandLine := '"' + AACCPath + '" --shutdown-for-update';
  StartupInfo.cb := SizeOf(StartupInfo);
  StartupInfo.dwFlags := STARTF_USESHOWWINDOW;
  StartupInfo.wShowWindow := SW_HIDE;
  if not CreateProcess(
    AACCPath,
    CommandLine,
    0,
    0,
    False,
    0,
    0,
    ExpandConstant('{app}'),
    StartupInfo,
    ProcessInfo
  ) then
    Exit;
  try
    CloseHandle(ProcessInfo.hThread);
    ProcessInfo.hThread := 0;
    WaitResult := WaitForSingleObject(
      ProcessInfo.hProcess,
      ShutdownControlTimeoutMilliseconds
    );
    if WaitResult = WAIT_TIMEOUT then
    begin
      { This handle belongs only to the newly created control invocation.
        It is never a handle to the existing main AACC process. }
      if not TerminateProcess(ProcessInfo.hProcess, 124) then
        Exit;
      if WaitForSingleObject(ProcessInfo.hProcess, 5000) <> WAIT_OBJECT_0 then
        Exit;
      Exit;
    end;
    if WaitResult <> WAIT_OBJECT_0 then
      Exit;
    if not GetExitCodeProcess(ProcessInfo.hProcess, ExitCode) then
      Exit;
    ResultCode := ExitCode;
    Result := True;
  finally
    if ProcessInfo.hThread <> 0 then
      CloseHandle(ProcessInfo.hThread);
    CloseHandle(ProcessInfo.hProcess);
  end;
end;

function ManifestPath(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '/', True);
end;

function ManifestContains(
  const Manifest: TArrayOfString;
  const Entry: String
): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 0 to GetArrayLength(Manifest) - 1 do
  begin
    if CompareText(Manifest[Index], Entry) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure CleanupInternalExtras(
  const RootPath: String;
  const RelativePath: String;
  const Manifest: TArrayOfString
);
var
  SearchPath: String;
  FullPath: String;
  ChildRelative: String;
  FindRec: TFindRec;
begin
  SearchPath := AddBackslash(RootPath);
  if RelativePath <> '' then
    SearchPath := SearchPath + RelativePath + '\';
  if not FindFirst(SearchPath + '*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
      begin
        if RelativePath = '' then
          ChildRelative := FindRec.Name
        else
          ChildRelative := RelativePath + '\' + FindRec.Name;
        FullPath := AddBackslash(RootPath) + ChildRelative;
        if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
        begin
          if (FindRec.Attributes and faDirectory) <> 0 then
          begin
            { Remove only the junction/reparse directory entry. Never recurse
              into a target that is outside the installed payload. }
            if not RemoveDir(FullPath) then
              Log('AACC_MANIFEST_CLEANUP result=reparse-directory-retained');
          end
          else if not DeleteFile(FullPath) then
            Log('AACC_MANIFEST_CLEANUP result=reparse-file-retained');
        end
        else if (FindRec.Attributes and faDirectory) <> 0 then
        begin
          CleanupInternalExtras(RootPath, ChildRelative, Manifest);
          if not ManifestContains(
            Manifest,
            'D ' + ManifestPath(ChildRelative) + '/'
          ) then
          begin
            if not RemoveDir(FullPath) then
              Log('AACC_MANIFEST_CLEANUP result=directory-retained');
          end;
        end
        else if not ManifestContains(
          Manifest,
          'F ' + ManifestPath(ChildRelative)
        ) then
        begin
          if not DeleteFile(FullPath) then
            Log('AACC_MANIFEST_CLEANUP result=file-retained');
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure CleanupCommittedInternalPayload;
var
  Manifest: TArrayOfString;
  ManifestPath: String;
begin
  ManifestPath := ExpandConstant('{app}\uninstall\internal-manifest-v1.txt');
  if not LoadStringsFromFile(ManifestPath, Manifest) then
  begin
    Log('AACC_MANIFEST_CLEANUP result=manifest-unavailable');
    Exit;
  end;
  CleanupInternalExtras(ExpandConstant('{app}\_internal'), '', Manifest);
  Log('AACC_MANIFEST_CLEANUP result=completed');
end;

function ShutdownExistingAACC(var ErrorMessage: String): Boolean;
var
  AACCPath: String;
  CapabilityPath: String;
  AACCWindow: HWND;
  ResultCode: Integer;
begin
  Result := True;
  ErrorMessage := '';
  AACCPath := ExpandConstant('{app}\AACC.exe');
  CapabilityPath := ExpandConstant('{app}\uninstall\') + ShutdownCapabilityName;
  if not FileExists(AACCPath) then
    Exit;

  AACCWindow := FindWindowByWindowName(AACCWindowTitle);
  if AACCWindow = 0 then
    Exit;

  if not FileExists(CapabilityPath) then
  begin
    ErrorMessage := ShutdownFailureMessage;
    Result := False;
    Exit;
  end;

  if not RunManagedShutdownControl(AACCPath, ResultCode) then
  begin
    ErrorMessage := ShutdownFailureMessage;
    Result := False;
    Exit;
  end;

  if (ResultCode <> 0) or
     (FindWindowByWindowName(AACCWindowTitle) <> 0) then
  begin
    ErrorMessage := ShutdownFailureMessage;
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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    try
      CleanupCommittedInternalPayload;
    except
      Log('AACC_MANIFEST_CLEANUP result=unexpected-retained');
    end;
  end;
end;
