#ifndef MyAppVersion
  #error MyAppVersion must be supplied by ISCC
#endif
#ifndef MyAppVersionInfo
  #error MyAppVersionInfo must be supplied by ISCC
#endif

[Setup]
AppId={{C174E242-E193-5863-8A46-F16152875173}
AppName=AACC
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersionInfo}
VersionInfoProductVersion={#MyAppVersionInfo}
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
Source: "..\build\installer\internal-manifest-v1.txt"; DestName: "aacc-preflight-manifest-v1.txt"; Flags: dontcopy noencryption
Source: "..\dist\AACC\AACC.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\aacc-spawn.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\AACC\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\installer\internal-manifest-v1.txt"; DestDir: "{app}\uninstall"; Flags: ignoreversion
Source: "shutdown-v1.capability"; DestDir: "{app}\uninstall"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AACC"; Filename: "{app}\AACC.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AACC.exe"; Description: "启动 AACC / Launch AACC"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent; Check: InternalCleanupSucceeded

[Code]
const
  AACCWindowTitle = 'AI Agent Control Center';
  ShutdownCapabilityName = 'shutdown-v1.capability';
  ShutdownControlTimeoutMilliseconds = 25000;
  AACC_WAIT_OBJECT_0 = 0;
  AACC_WAIT_TIMEOUT = 258;
  AACC_STARTF_USESHOWWINDOW = 1;
  AACC_INVALID_FILE_ATTRIBUTES = $FFFFFFFF;
  AACC_INVALID_HANDLE_VALUE = $FFFFFFFF;
  AACC_ERROR_FILE_NOT_FOUND = 2;
  AACC_ERROR_PATH_NOT_FOUND = 3;
  AACC_GENERIC_READ = $80000000;
  AACC_GENERIC_WRITE = $40000000;
  AACC_DELETE_ACCESS = $00010000;
  AACC_OPEN_EXISTING = 3;
  AACC_FILE_ATTRIBUTE_NORMAL = $80;
  AACC_FILE_FLAG_BACKUP_SEMANTICS = $02000000;
  PreflightRetryCount = 5;
  PreflightRetryDelayMilliseconds = 1000;
  CleanupRetryCount = 3;
  CleanupRetryDelayMilliseconds = 250;
  InternalCleanupFailureExitCode = 9;

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
function WinGetFileAttributes(lpFileName: String): DWORD;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function WinCreateFile(
  lpFileName: String;
  dwDesiredAccess: DWORD;
  dwShareMode: DWORD;
  lpSecurityAttributes: LongWord;
  dwCreationDisposition: DWORD;
  dwFlagsAndAttributes: DWORD;
  hTemplateFile: THandle
): THandle;
  external 'CreateFileW@kernel32.dll stdcall';

var
  InternalCleanupIncomplete: Boolean;

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
  StartupInfo.dwFlags := AACC_STARTF_USESHOWWINDOW;
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
    if WaitResult = AACC_WAIT_TIMEOUT then
    begin
      { This handle belongs only to the newly created control invocation.
        It is never a handle to the existing main AACC process. }
      if not TerminateProcess(ProcessInfo.hProcess, 124) then
        Exit;
      if WaitForSingleObject(ProcessInfo.hProcess, 5000) <> AACC_WAIT_OBJECT_0 then
        Exit;
      Exit;
    end;
    if WaitResult <> AACC_WAIT_OBJECT_0 then
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

function ValidateInternalManifest(const Manifest: TArrayOfString): Boolean;
var
  Index: Integer;
  PriorIndex: Integer;
  EntryPath: String;
  PriorPath: String;
  FramedPath: String;
begin
  Result := False;
  if GetArrayLength(Manifest) = 0 then
    Exit;
  for Index := 0 to GetArrayLength(Manifest) - 1 do
  begin
    if (Length(Manifest[Index]) < 3) or
       ((Copy(Manifest[Index], 1, 2) <> 'D ') and
        (Copy(Manifest[Index], 1, 2) <> 'F ')) then
      Exit;
    EntryPath := Copy(
      Manifest[Index],
      3,
      Length(Manifest[Index]) - 2
    );
    if Copy(Manifest[Index], 1, 2) = 'D ' then
    begin
      if EntryPath = '' then
        Exit;
      if EntryPath[Length(EntryPath)] <> '/' then
        Exit;
      Delete(EntryPath, Length(EntryPath), 1);
    end
    else
    begin
      if EntryPath = '' then
        Exit;
      if EntryPath[Length(EntryPath)] = '/' then
        Exit;
    end;
    if EntryPath = '' then
      Exit;
    FramedPath := '/' + EntryPath + '/';
    if (EntryPath[1] = '/') or
       (Pos('\', EntryPath) <> 0) or
       (Pos(':', EntryPath) <> 0) or
       (Pos('//', FramedPath) <> 0) or
       (Pos('/./', FramedPath) <> 0) or
       (Pos('/../', FramedPath) <> 0) then
      Exit;
    for PriorIndex := 0 to Index - 1 do
    begin
      PriorPath := Copy(
        Manifest[PriorIndex],
        3,
        Length(Manifest[PriorIndex]) - 2
      );
      if PriorPath <> '' then
        if PriorPath[Length(PriorPath)] = '/' then
          Delete(PriorPath, Length(PriorPath), 1);
      if CompareText(PriorPath, EntryPath) = 0 then
        Exit;
    end;
  end;
  Result := True;
end;

function PreflightFailureMessage: String;
begin
  Result :=
    'AACC 的现有程序文件正在使用或不可安全替换。请完全退出 AACC，然后重试。' + #13#10 +
    'Existing AACC program files are in use or cannot be replaced safely. ' +
    'Exit AACC completely and retry.';
end;

function ValidateExistingManagedDirectory(const Path: String): Boolean;
var
  Attributes: DWORD;
  ErrorCode: DWORD;
begin
  Result := False;
  Attributes := WinGetFileAttributes(Path);
  if Attributes = AACC_INVALID_FILE_ATTRIBUTES then
  begin
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
      Result := True;
    Exit;
  end;
  Result :=
    ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) and
    ((Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0);
end;

function ValidateExistingTargetFile(const Path: String): Boolean;
var
  Attributes: DWORD;
  ErrorCode: DWORD;
  Attempt: Integer;
  FileHandle: THandle;
begin
  Result := False;
  Attributes := WinGetFileAttributes(Path);
  if Attributes = AACC_INVALID_FILE_ATTRIBUTES then
  begin
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
      Result := True;
    Exit;
  end;
  if ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) or
     ((Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) then
    Exit;
  for Attempt := 1 to PreflightRetryCount do
  begin
    FileHandle := WinCreateFile(
      Path,
      AACC_GENERIC_READ or AACC_GENERIC_WRITE or AACC_DELETE_ACCESS,
      0,
      0,
      AACC_OPEN_EXISTING,
      AACC_FILE_ATTRIBUTE_NORMAL,
      0
    );
    if FileHandle <> AACC_INVALID_HANDLE_VALUE then
    begin
      CloseHandle(FileHandle);
      Result := True;
      Exit;
    end;
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
    begin
      Result := True;
      Exit;
    end;
    if Attempt < PreflightRetryCount then
      Sleep(PreflightRetryDelayMilliseconds);
  end;
end;

function ValidateExistingTargetDirectory(const Path: String): Boolean;
var
  Attributes: DWORD;
  ErrorCode: DWORD;
  Attempt: Integer;
  DirectoryHandle: THandle;
begin
  Result := False;
  Attributes := WinGetFileAttributes(Path);
  if Attributes = AACC_INVALID_FILE_ATTRIBUTES then
  begin
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
      Result := True;
    Exit;
  end;
  if ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) or
     ((Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) then
    Exit;
  for Attempt := 1 to PreflightRetryCount do
  begin
    DirectoryHandle := WinCreateFile(
      Path,
      AACC_GENERIC_READ or AACC_DELETE_ACCESS,
      0,
      0,
      AACC_OPEN_EXISTING,
      AACC_FILE_FLAG_BACKUP_SEMANTICS,
      0
    );
    if DirectoryHandle <> AACC_INVALID_HANDLE_VALUE then
    begin
      CloseHandle(DirectoryHandle);
      Result := True;
      Exit;
    end;
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
    begin
      Result := True;
      Exit;
    end;
    if Attempt < PreflightRetryCount then
      Sleep(PreflightRetryDelayMilliseconds);
  end;
end;

function ValidateInternalTargetParents(
  const RootPath: String;
  RelativePath: String
): Boolean;
var
  SeparatorIndex: Integer;
  ParentPath: String;
  Segment: String;
begin
  Result := False;
  ParentPath := RootPath;
  SeparatorIndex := Pos('/', RelativePath);
  while SeparatorIndex > 0 do
  begin
    Segment := Copy(RelativePath, 1, SeparatorIndex - 1);
    if Segment = '' then
      Exit;
    ParentPath := AddBackslash(ParentPath) + Segment;
    if not ValidateExistingManagedDirectory(ParentPath) then
      Exit;
    Delete(RelativePath, 1, SeparatorIndex);
    SeparatorIndex := Pos('/', RelativePath);
  end;
  Result := True;
end;

function ValidateInternalManifestTargets(
  const Manifest: TArrayOfString;
  const RootPath: String
): Boolean;
var
  Index: Integer;
  RelativePath: String;
  FullPath: String;
  IsDirectory: Boolean;
begin
  Result := False;
  for Index := 0 to GetArrayLength(Manifest) - 1 do
  begin
    IsDirectory := Copy(Manifest[Index], 1, 2) = 'D ';
    RelativePath := Copy(
      Manifest[Index],
      3,
      Length(Manifest[Index]) - 2
    );
    if IsDirectory then
      Delete(RelativePath, Length(RelativePath), 1);
    if not ValidateInternalTargetParents(RootPath, RelativePath) then
      Exit;
    StringChangeEx(RelativePath, '/', '\', True);
    FullPath := AddBackslash(RootPath) + RelativePath;
    if IsDirectory then
    begin
      if not ValidateExistingTargetDirectory(FullPath) then
        Exit;
    end
    else if not ValidateExistingTargetFile(FullPath) then
      Exit;
  end;
  Result := True;
end;

function ValidateExistingUninstallerTargets(const RootPath: String): Boolean;
var
  Index: Integer;
  BaseName: String;
begin
  Result := False;
  for Index := 0 to 999 do
  begin
    BaseName := AddBackslash(RootPath) + Format('unins%.3d', [Index]);
    if not ValidateExistingTargetFile(BaseName + '.exe') or
       not ValidateExistingTargetFile(BaseName + '.dat') then
      Exit;
  end;
  Result := True;
end;

function ValidatePackagedTargetsForInstall(var ErrorMessage: String): Boolean;
var
  Manifest: TArrayOfString;
  ManifestFilePath: String;
  InstalledManifest: TArrayOfString;
  InstalledManifestPath: String;
  InternalRoot: String;
begin
  Result := False;
  ErrorMessage := PreflightFailureMessage;
  try
    ExtractTemporaryFile('aacc-preflight-manifest-v1.txt');
  except
    Log('AACC_PREFLIGHT result=manifest-unavailable');
    Exit;
  end;
  ManifestFilePath :=
    ExpandConstant('{tmp}\aacc-preflight-manifest-v1.txt');
  if not LoadStringsFromFile(ManifestFilePath, Manifest) then
  begin
    Log('AACC_PREFLIGHT result=manifest-unavailable');
    Exit;
  end;
  if not ValidateInternalManifest(Manifest) then
  begin
    Log('AACC_PREFLIGHT result=manifest-invalid');
    Exit;
  end;
  InternalRoot := ExpandConstant('{app}\_internal');
  InstalledManifestPath :=
    ExpandConstant('{app}\uninstall\internal-manifest-v1.txt');
  if not ValidateExistingTargetDirectory(ExpandConstant('{app}')) or
     not ValidateExistingTargetDirectory(ExpandConstant('{app}\uninstall')) or
     not ValidateExistingTargetDirectory(InternalRoot) or
     not ValidateExistingUninstallerTargets(
       ExpandConstant('{app}\uninstall')
     ) or
     not ValidateExistingTargetFile(ExpandConstant('{app}\AACC.exe')) or
     not ValidateExistingTargetFile(ExpandConstant('{app}\aacc-spawn.exe')) or
     not ValidateExistingTargetFile(InstalledManifestPath) or
     not ValidateExistingTargetFile(
       ExpandConstant('{app}\uninstall\shutdown-v1.capability')
     ) or
     not ValidateExistingTargetFile(
       ExpandConstant('{autoprograms}\AACC.lnk')
     ) or
     not ValidateExistingTargetFile(
       ExpandConstant('{autodesktop}\AACC.lnk')
     ) or
     not ValidateInternalManifestTargets(Manifest, InternalRoot) then
  begin
    Log('AACC_PREFLIGHT result=target-unavailable');
    Exit;
  end;
  if DirExists(InternalRoot) then
  begin
    if not LoadStringsFromFile(InstalledManifestPath, InstalledManifest) then
    begin
      Log('AACC_PREFLIGHT result=installed-manifest-unavailable');
      Exit;
    end;
    if not ValidateInternalManifest(InstalledManifest) then
    begin
      Log('AACC_PREFLIGHT result=installed-manifest-invalid');
      Exit;
    end;
    if not ValidateInternalManifestTargets(InstalledManifest, InternalRoot) then
    begin
      Log('AACC_PREFLIGHT result=target-unavailable');
      Exit;
    end;
  end;
  Log('AACC_PREFLIGHT result=completed');
  ErrorMessage := '';
  Result := True;
end;

function DeleteFileWithRetries(const Path: String): Boolean;
var
  Attempt: Integer;
begin
  Result := False;
  for Attempt := 1 to CleanupRetryCount do
  begin
    if DeleteFile(Path) then
    begin
      Result := True;
      Exit;
    end;
    if Attempt < CleanupRetryCount then
      Sleep(CleanupRetryDelayMilliseconds);
  end;
end;

function RemoveDirWithRetries(const Path: String): Boolean;
var
  Attempt: Integer;
begin
  Result := False;
  for Attempt := 1 to CleanupRetryCount do
  begin
    if RemoveDir(Path) then
    begin
      Result := True;
      Exit;
    end;
    if Attempt < CleanupRetryCount then
      Sleep(CleanupRetryDelayMilliseconds);
  end;
end;

function CleanupInternalExtras(
  const RootPath: String;
  const RelativePath: String;
  const Manifest: TArrayOfString
): Boolean;
var
  SearchPath: String;
  FullPath: String;
  ChildRelative: String;
  FindRec: TFindRec;
begin
  Result := True;
  if not DirExists(RootPath) then
  begin
    Result := False;
    Exit;
  end;
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
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            { Remove only the junction/reparse directory entry. Never recurse
              into a target that is outside the installed payload. }
            if not RemoveDirWithRetries(FullPath) then
            begin
              Log('AACC_MANIFEST_CLEANUP result=reparse-directory-retained');
              Result := False;
            end;
          end
          else if not DeleteFileWithRetries(FullPath) then
          begin
            Log('AACC_MANIFEST_CLEANUP result=reparse-file-retained');
            Result := False;
          end;
        end
        else if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if not CleanupInternalExtras(RootPath, ChildRelative, Manifest) then
            Result := False;
          if not ManifestContains(
            Manifest,
            'D ' + ManifestPath(ChildRelative) + '/'
          ) then
          begin
            if not RemoveDirWithRetries(FullPath) then
            begin
              Log('AACC_MANIFEST_CLEANUP result=directory-retained');
              Result := False;
            end;
          end;
        end
        else if not ManifestContains(
          Manifest,
          'F ' + ManifestPath(ChildRelative)
        ) then
        begin
          if not DeleteFileWithRetries(FullPath) then
          begin
            Log('AACC_MANIFEST_CLEANUP result=file-retained');
            Result := False;
          end;
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function CleanupCommittedInternalPayload: Boolean;
var
  Manifest: TArrayOfString;
  ManifestFilePath: String;
begin
  Result := False;
  ManifestFilePath := ExpandConstant('{app}\uninstall\internal-manifest-v1.txt');
  if not LoadStringsFromFile(ManifestFilePath, Manifest) then
  begin
    Log('AACC_MANIFEST_CLEANUP result=manifest-unavailable');
    Exit;
  end;
  if not ValidateInternalManifest(Manifest) then
  begin
    Log('AACC_MANIFEST_CLEANUP result=manifest-invalid');
    Exit;
  end;
  if not CleanupInternalExtras(
    ExpandConstant('{app}\_internal'),
    '',
    Manifest
  ) then
    Exit;
  Log('AACC_MANIFEST_CLEANUP result=completed');
  Result := True;
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

function ValidateInternalRootForInstall(var ErrorMessage: String): Boolean;
var
  InstallRoot: String;
  InternalRoot: String;
  Attributes: DWORD;
  ErrorCode: DWORD;
begin
  Result := False;
  ErrorMessage := '';
  InstallRoot := ExpandConstant('{app}');
  if not ValidateExistingManagedDirectory(InstallRoot) then
  begin
    ErrorMessage :=
      'AACC 程序目录不安全；安装已在写入前停止。' + #13#10 +
      'AACC program root is unsafe; Setup stopped before writing.';
    Exit;
  end;
  InternalRoot := ExpandConstant('{app}\_internal');
  Attributes := WinGetFileAttributes(InternalRoot);
  if Attributes = AACC_INVALID_FILE_ATTRIBUTES then
  begin
    ErrorCode := DLLGetLastError;
    if (ErrorCode = AACC_ERROR_FILE_NOT_FOUND) or
       (ErrorCode = AACC_ERROR_PATH_NOT_FOUND) then
    begin
      Result := True;
      Exit;
    end;
  end
  else if ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) and
          ((Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) then
  begin
    Result := True;
    Exit;
  end;
  ErrorMessage :=
    'AACC 内部程序目录不安全；安装已在写入前停止。' + #13#10 +
    'AACC internal payload root is unsafe; Setup stopped before writing.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ErrorMessage: String;
begin
  NeedsRestart := False;
  if not ValidateInternalRootForInstall(ErrorMessage) then
  begin
    Result := ErrorMessage;
    Exit;
  end;
  if not ShutdownExistingAACC(ErrorMessage) then
  begin
    Result := ErrorMessage;
    Exit;
  end;
  if not ValidatePackagedTargetsForInstall(ErrorMessage) then
  begin
    Result := ErrorMessage;
    Exit;
  end;
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
    if not CleanupCommittedInternalPayload then
    begin
      InternalCleanupIncomplete := True;
      Log('AACC_MANIFEST_CLEANUP result=incomplete');
    end;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if InternalCleanupIncomplete then
    Result := InternalCleanupFailureExitCode
  else
    Result := 0;
end;

function InternalCleanupSucceeded: Boolean;
begin
  Result := not InternalCleanupIncomplete;
end;
