#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <cwchar>
#include <cwctype>
#include <string>
#include <utility>
#include <vector>

#include "aacc_spawn_version.h"

namespace {

constexpr int kEnvironmentStage = 12;
constexpr int kJobAssignStage = 23;
constexpr int kResumeStage = 24;
constexpr wchar_t kBrokerCodexTargetName[] = L"AACC_BROKER_CODEX_TARGET";

struct Options {
    DWORD parent_pid = 0;
    std::wstring bundle_dir;
    std::wstring codex_path;
};

class UniqueHandle {
  public:
    UniqueHandle() noexcept = default;
    explicit UniqueHandle(HANDLE handle) noexcept : handle_(handle) {}

    ~UniqueHandle() {
        const DWORD error = GetLastError();
        Reset();
        SetLastError(error);
    }

    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;

    UniqueHandle(UniqueHandle&& other) noexcept : handle_(other.Release()) {}

    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) {
            Reset(other.Release());
        }
        return *this;
    }

    [[nodiscard]] HANDLE Get() const noexcept {
        return handle_;
    }

    [[nodiscard]] bool IsValid() const noexcept {
        return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
    }

    HANDLE Release() noexcept {
        HANDLE released = handle_;
        handle_ = nullptr;
        return released;
    }

    void Reset(HANDLE replacement = nullptr) noexcept {
        if (IsValid()) {
            CloseHandle(handle_);
        }
        handle_ = replacement;
    }

  private:
    HANDLE handle_ = nullptr;
};

class ProcThreadAttributeList {
  public:
    ProcThreadAttributeList() noexcept = default;

    ~ProcThreadAttributeList() {
        const DWORD error = GetLastError();
        if (list_ != nullptr) {
            DeleteProcThreadAttributeList(list_);
            HeapFree(GetProcessHeap(), 0, list_);
        }
        SetLastError(error);
    }

    ProcThreadAttributeList(const ProcThreadAttributeList&) = delete;
    ProcThreadAttributeList& operator=(const ProcThreadAttributeList&) = delete;

    bool Initialize() noexcept {
        SIZE_T bytes = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
        if (bytes == 0) {
            return false;
        }
        list_ = static_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(
            HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, bytes));
        if (list_ == nullptr) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return false;
        }
        if (!InitializeProcThreadAttributeList(list_, 1, 0, &bytes)) {
            const DWORD error = GetLastError();
            HeapFree(GetProcessHeap(), 0, list_);
            list_ = nullptr;
            SetLastError(error);
            return false;
        }
        return true;
    }

    [[nodiscard]] LPPROC_THREAD_ATTRIBUTE_LIST Get() const noexcept {
        return list_;
    }

  private:
    LPPROC_THREAD_ATTRIBUTE_LIST list_ = nullptr;
};

class EnvironmentStrings {
  public:
    explicit EnvironmentStrings(LPWCH strings) noexcept : strings_(strings) {}

    ~EnvironmentStrings() {
        const DWORD error = GetLastError();
        if (strings_ != nullptr) {
            FreeEnvironmentStringsW(strings_);
        }
        SetLastError(error);
    }

    EnvironmentStrings(const EnvironmentStrings&) = delete;
    EnvironmentStrings& operator=(const EnvironmentStrings&) = delete;

    [[nodiscard]] LPWCH Get() const noexcept {
        return strings_;
    }

  private:
    LPWCH strings_ = nullptr;
};

struct EnvironmentBlock {
    std::vector<wchar_t> characters;
};

struct TargetCommand {
    std::wstring application_name;
    std::wstring command_line;
};

struct SuspendedProcess {
    UniqueHandle process;
    UniqueHandle thread;
};

int Fail(int stage, DWORD error) {
    fwprintf(stderr, L"AACC_BROKER_ERROR stage=%d win32=%lu\n", stage, error);
    return stage;
}

bool IsSeparator(wchar_t character) noexcept {
    return character == L'\\' || character == L'/';
}

bool IsDriveAbsolute(const std::wstring& path, size_t offset = 0) noexcept {
    return path.size() >= offset + 3 &&
           ((path[offset] >= L'A' && path[offset] <= L'Z') ||
            (path[offset] >= L'a' && path[offset] <= L'z')) &&
           path[offset + 1] == L':' && IsSeparator(path[offset + 2]);
}

bool HasUncServerAndShare(const std::wstring& path, size_t offset) noexcept {
    const size_t server_end = path.find_first_of(L"\\/", offset);
    if (server_end == std::wstring::npos || server_end == offset) {
        return false;
    }
    const size_t share_start = server_end + 1;
    if (share_start >= path.size() || IsSeparator(path[share_start])) {
        return false;
    }
    return true;
}

bool IsAbsolutePath(const std::wstring& path) noexcept {
    if (IsDriveAbsolute(path)) {
        return true;
    }
    if (path.size() >= 4 && IsSeparator(path[0]) && IsSeparator(path[1]) &&
        path[2] == L'?' && IsSeparator(path[3])) {
        if (IsDriveAbsolute(path, 4)) {
            return true;
        }
        if (path.size() >= 8 &&
            CompareStringOrdinal(path.data() + 4, 4, L"UNC\\", 4, TRUE) == CSTR_EQUAL) {
            return HasUncServerAndShare(path, 8);
        }
        return false;
    }
    if (path.size() >= 4 && IsSeparator(path[0]) && IsSeparator(path[1])) {
        if (path[2] == L'.' && IsSeparator(path[3])) {
            return false;
        }
        return HasUncServerAndShare(path, 2);
    }
    return false;
}

bool EqualsOrdinalIgnoreCase(
    const wchar_t* left,
    int left_length,
    const wchar_t* right,
    int right_length) noexcept {
    return CompareStringOrdinal(left, left_length, right, right_length, TRUE) == CSTR_EQUAL;
}

bool HasAllowedTargetExtension(const std::wstring& path) noexcept {
    const size_t separator = path.find_last_of(L"\\/");
    const size_t dot = path.find_last_of(L'.');
    if (dot == std::wstring::npos ||
        (separator != std::wstring::npos && dot < separator)) {
        return false;
    }
    const std::wstring extension = path.substr(dot);
    return _wcsicmp(extension.c_str(), L".exe") == 0 ||
           _wcsicmp(extension.c_str(), L".cmd") == 0 ||
           _wcsicmp(extension.c_str(), L".bat") == 0;
}

bool IsExtendedOrDevicePath(const std::wstring& path) noexcept {
    return path.size() >= 4 && IsSeparator(path[0]) &&
           IsSeparator(path[1]) &&
           (path[2] == L'?' || path[2] == L'.') &&
           IsSeparator(path[3]);
}

bool HasSafeBrokerPathSyntax(const std::wstring& path) noexcept {
    if (IsExtendedOrDevicePath(path)) {
        return false;
    }
    for (size_t index = 0; index < path.size(); ++index) {
        const wchar_t character = path[index];
        if (character >= 0x0001 && character <= 0x001F) {
            return false;
        }
        if (character == L':' && index != 1) {
            return false;
        }
    }
    return true;
}

bool ParsePid(const std::wstring& value, DWORD* pid) noexcept {
    if (value.empty()) {
        return false;
    }
    unsigned long long parsed = 0;
    for (const wchar_t character : value) {
        if (character < L'0' || character > L'9') {
            return false;
        }
        parsed = parsed * 10 + static_cast<unsigned long long>(character - L'0');
        if (parsed > MAXDWORD) {
            return false;
        }
    }
    if (parsed == 0) {
        return false;
    }
    *pid = static_cast<DWORD>(parsed);
    return true;
}

bool ValidateOptions(const Options& options) noexcept {
    if (options.parent_pid == 0 ||
        !HasSafeBrokerPathSyntax(options.bundle_dir) ||
        !HasSafeBrokerPathSyntax(options.codex_path) ||
        !IsAbsolutePath(options.bundle_dir) ||
        !IsAbsolutePath(options.codex_path) ||
        !HasAllowedTargetExtension(options.codex_path)) {
        return false;
    }

    const DWORD bundle_attributes = GetFileAttributesW(options.bundle_dir.c_str());
    if (bundle_attributes == INVALID_FILE_ATTRIBUTES ||
        (bundle_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
        return false;
    }

    const DWORD codex_attributes = GetFileAttributesW(options.codex_path.c_str());
    return codex_attributes != INVALID_FILE_ATTRIBUTES &&
           (codex_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
}

bool ParseOptions(int argc, wchar_t* argv[], Options* options) {
    if (argc != 9) {
        return false;
    }

    bool protocol_seen = false;
    bool parent_seen = false;
    bool bundle_seen = false;
    bool codex_seen = false;

    for (int index = 1; index < argc; index += 2) {
        const std::wstring flag(argv[index]);
        const std::wstring value(argv[index + 1]);
        if (flag == L"--protocol" && !protocol_seen) {
            protocol_seen = true;
            if (value != L"1") {
                return false;
            }
        } else if (flag == L"--parent-pid" && !parent_seen) {
            parent_seen = true;
            if (!ParsePid(value, &options->parent_pid)) {
                return false;
            }
        } else if (flag == L"--bundle-dir" && !bundle_seen) {
            bundle_seen = true;
            options->bundle_dir = value;
        } else if (flag == L"--codex" && !codex_seen) {
            codex_seen = true;
            options->codex_path = value;
        } else {
            return false;
        }
    }

    return protocol_seen && parent_seen && bundle_seen && codex_seen &&
           ValidateOptions(*options);
}

void ReplaceSeparators(std::wstring* path) {
    std::replace(path->begin(), path->end(), L'/', L'\\');
}

bool IsDriveRoot(const std::wstring& path) noexcept {
    return (path.size() == 3 && IsDriveAbsolute(path)) ||
           (path.size() == 7 && path.size() >= 4 && path[0] == L'\\' &&
            path[1] == L'\\' && path[2] == L'?' && path[3] == L'\\' &&
            IsDriveAbsolute(path, 4));
}

void TrimTrailingSeparators(std::wstring* path) {
    while (!path->empty() && IsSeparator(path->back()) && !IsDriveRoot(*path)) {
        path->pop_back();
    }
}

std::wstring TrimAndUnquotePathEntry(const std::wstring& entry) {
    size_t start = 0;
    size_t end = entry.size();
    while (start < end && iswspace(entry[start]) != 0) {
        ++start;
    }
    while (end > start && iswspace(entry[end - 1]) != 0) {
        --end;
    }
    if (end - start >= 2 && entry[start] == L'"' && entry[end - 1] == L'"') {
        ++start;
        --end;
    }
    return entry.substr(start, end - start);
}

bool NormalizePath(const std::wstring& original, std::wstring* normalized) {
    const std::wstring unquoted = TrimAndUnquotePathEntry(original);
    if (unquoted.empty()) {
        wchar_t current_directory[MAX_PATH + 1] = {};
        const DWORD length = GetCurrentDirectoryW(MAX_PATH + 1, current_directory);
        if (length == 0 || length > MAX_PATH) {
            return false;
        }
        *normalized = current_directory;
    } else {
        const DWORD required = GetFullPathNameW(unquoted.c_str(), 0, nullptr, nullptr);
        if (required == 0) {
            return false;
        }
        std::vector<wchar_t> buffer(static_cast<size_t>(required), L'\0');
        const DWORD length =
            GetFullPathNameW(unquoted.c_str(), required, buffer.data(), nullptr);
        if (length == 0 || length >= required) {
            return false;
        }
        normalized->assign(buffer.data(), length);
    }
    ReplaceSeparators(normalized);
    TrimTrailingSeparators(normalized);
    return !normalized->empty();
}

bool IsPathRootedIn(
    const std::wstring& candidate,
    const std::wstring& normalized_root) noexcept {
    if (candidate.size() < normalized_root.size() ||
        !EqualsOrdinalIgnoreCase(
            candidate.data(),
            static_cast<int>(normalized_root.size()),
            normalized_root.data(),
            static_cast<int>(normalized_root.size()))) {
        return false;
    }
    if (candidate.size() == normalized_root.size() ||
        IsSeparator(normalized_root.back())) {
        return true;
    }
    return IsSeparator(candidate[normalized_root.size()]);
}

std::wstring SanitizePathValue(
    const std::wstring& value,
    const std::wstring& normalized_bundle) {
    std::wstring sanitized;
    size_t start = 0;
    bool first = true;
    while (start <= value.size()) {
        const size_t separator = value.find(L';', start);
        const size_t length =
            separator == std::wstring::npos ? value.size() - start : separator - start;
        const std::wstring entry = value.substr(start, length);
        std::wstring normalized_entry;
        const bool remove =
            NormalizePath(entry, &normalized_entry) &&
            IsPathRootedIn(normalized_entry, normalized_bundle);
        if (!remove) {
            if (!first) {
                sanitized.push_back(L';');
            }
            sanitized.append(entry);
            first = false;
        }
        if (separator == std::wstring::npos) {
            break;
        }
        start = separator + 1;
    }
    return sanitized;
}

bool EnvironmentEntryLess(
    const std::wstring& left,
    const std::wstring& right) noexcept {
    const int comparison = CompareStringOrdinal(
        left.data(),
        static_cast<int>(left.size()),
        right.data(),
        static_cast<int>(right.size()),
        TRUE);
    if (comparison == CSTR_LESS_THAN) {
        return true;
    }
    if (comparison == CSTR_GREATER_THAN) {
        return false;
    }
    return left < right;
}

bool BuildSanitizedEnvironment(
    const std::wstring& bundle_dir,
    const std::wstring& codex_path,
    EnvironmentBlock* output) {
    std::wstring normalized_bundle;
    if (!NormalizePath(bundle_dir, &normalized_bundle)) {
        SetLastError(ERROR_INVALID_NAME);
        return false;
    }

    EnvironmentStrings inherited(GetEnvironmentStringsW());
    if (inherited.Get() == nullptr) {
        return false;
    }

    std::vector<std::wstring> entries;
    const std::wstring target_prefix =
        std::wstring(kBrokerCodexTargetName) + L"=";
    for (const wchar_t* current = inherited.Get(); *current != L'\0';
         current += wcslen(current) + 1) {
        std::wstring entry(current);
        if (entry.size() >= target_prefix.size() &&
            EqualsOrdinalIgnoreCase(
                entry.data(),
                static_cast<int>(target_prefix.size()),
                target_prefix.data(),
                static_cast<int>(target_prefix.size()))) {
            continue;
        }
        if (entry.size() >= 5 &&
            EqualsOrdinalIgnoreCase(entry.data(), 5, L"PATH=", 5)) {
            entry = entry.substr(0, 5) +
                    SanitizePathValue(entry.substr(5), normalized_bundle);
        }
        entries.push_back(std::move(entry));
    }
    // cmd expands this reserved private variable exactly once. Replacing any
    // inherited value prevents injection; user variables other than PATH stay
    // unchanged.
    entries.push_back(target_prefix + codex_path);

    std::sort(entries.begin(), entries.end(), EnvironmentEntryLess);
    output->characters.clear();
    for (const std::wstring& entry : entries) {
        output->characters.insert(
            output->characters.end(), entry.begin(), entry.end());
        output->characters.push_back(L'\0');
    }
    if (entries.empty()) {
        output->characters.push_back(L'\0');
    }
    output->characters.push_back(L'\0');
    return true;
}

std::wstring QuoteWindowsArgument(const std::wstring& argument) {
    std::wstring quoted;
    quoted.push_back(L'"');
    size_t backslashes = 0;
    for (const wchar_t character : argument) {
        if (character == L'\\') {
            ++backslashes;
            continue;
        }
        if (character == L'"') {
            quoted.append(backslashes * 2 + 1, L'\\');
            quoted.push_back(L'"');
            backslashes = 0;
            continue;
        }
        quoted.append(backslashes, L'\\');
        backslashes = 0;
        quoted.push_back(character);
    }
    quoted.append(backslashes * 2, L'\\');
    quoted.push_back(L'"');
    return quoted;
}

bool ResolveSystemCmd(std::wstring* cmd_path) {
    std::array<wchar_t, MAX_PATH + 1> system_directory = {};
    const UINT length = GetSystemDirectoryW(
        system_directory.data(), static_cast<UINT>(system_directory.size()));
    if (length == 0) {
        return false;
    }
    if (length >= static_cast<UINT>(system_directory.size())) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return false;
    }
    cmd_path->assign(system_directory.data(), length);
    if (!cmd_path->empty() && !IsSeparator(cmd_path->back())) {
        cmd_path->push_back(L'\\');
    }
    cmd_path->append(L"cmd.exe");
    return true;
}

std::wstring BuildCmdScriptCommandLine(const std::wstring& cmd_path) {
    // /S removes the outer pair around the /C string. cmd expands variables
    // once (not recursively), and /V:OFF disables !name! expansion. The
    // resulting script path remains quoted, so metacharacters are data.
    const std::wstring target_reference =
        L"%" + std::wstring(kBrokerCodexTargetName) + L"%";
    return QuoteWindowsArgument(cmd_path) + L" /D /V:OFF /S /C \"\"" +
           target_reference + L"\" app-server --stdio\"";
}

bool BuildCodexCommand(const std::wstring& codex_path, TargetCommand* command) {
    const size_t dot = codex_path.find_last_of(L'.');
    const std::wstring extension =
        dot == std::wstring::npos ? std::wstring() : codex_path.substr(dot);
    const std::wstring app_server = L"app-server";
    const std::wstring stdio = L"--stdio";

    if (_wcsicmp(extension.c_str(), L".exe") == 0) {
        command->application_name = codex_path;
        command->command_line = QuoteWindowsArgument(codex_path) + L" " +
                                QuoteWindowsArgument(app_server) + L" " +
                                QuoteWindowsArgument(stdio);
        return true;
    }

    if (!ResolveSystemCmd(&command->application_name)) {
        return false;
    }

    command->command_line =
        BuildCmdScriptCommandLine(command->application_name);
    return true;
}

bool DuplicateInheritableHandle(HANDLE source, UniqueHandle* duplicate) noexcept {
    if (source == nullptr || source == INVALID_HANDLE_VALUE) {
        SetLastError(ERROR_INVALID_HANDLE);
        return false;
    }
    HANDLE result = nullptr;
    if (!DuplicateHandle(
            GetCurrentProcess(),
            source,
            GetCurrentProcess(),
            &result,
            0,
            TRUE,
            DUPLICATE_SAME_ACCESS)) {
        return false;
    }
    duplicate->Reset(result);
    return true;
}

bool CreateSuspendedWithStdio(
    const TargetCommand& command,
    EnvironmentBlock* environment,
    SuspendedProcess* target) {
    UniqueHandle child_stdin;
    UniqueHandle child_stdout;
    if (!DuplicateInheritableHandle(GetStdHandle(STD_INPUT_HANDLE), &child_stdin) ||
        !DuplicateInheritableHandle(GetStdHandle(STD_OUTPUT_HANDLE), &child_stdout)) {
        return false;
    }

    SECURITY_ATTRIBUTES inheritable = {};
    inheritable.nLength = sizeof(inheritable);
    inheritable.bInheritHandle = TRUE;
    UniqueHandle child_stderr(CreateFileW(
        L"NUL",
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        &inheritable,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr));
    if (!child_stderr.IsValid()) {
        return false;
    }

    std::array<HANDLE, 3> inherited_handles = {
        child_stdin.Get(), child_stdout.Get(), child_stderr.Get()};
    ProcThreadAttributeList attributes;
    if (!attributes.Initialize()) {
        return false;
    }
    if (!UpdateProcThreadAttribute(
            attributes.Get(),
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            inherited_handles.data(),
            sizeof(inherited_handles),
            nullptr,
            nullptr)) {
        return false;
    }

    STARTUPINFOEXW startup = {};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = child_stdin.Get();
    startup.StartupInfo.hStdOutput = child_stdout.Get();
    startup.StartupInfo.hStdError = child_stderr.Get();
    startup.lpAttributeList = attributes.Get();

    std::vector<wchar_t> mutable_command(
        command.command_line.begin(), command.command_line.end());
    mutable_command.push_back(L'\0');
    PROCESS_INFORMATION process_information = {};
    const DWORD creation_flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED |
                                 CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT;
    if (!CreateProcessW(
            command.application_name.c_str(),
            mutable_command.data(),
            nullptr,
            nullptr,
            TRUE,
            creation_flags,
            environment->characters.data(),
            nullptr,
            &startup.StartupInfo,
            &process_information)) {
        return false;
    }

    target->process.Reset(process_information.hProcess);
    target->thread.Reset(process_information.hThread);
    return true;
}

bool ConfigureKillOnClose(HANDLE job) noexcept {
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION information = {};
    information.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    return SetInformationJobObject(
               job,
               JobObjectExtendedLimitInformation,
               &information,
               sizeof(information)) != FALSE;
}

int WaitForParentOrTarget(HANDLE parent, HANDLE target, HANDLE job) {
    const std::array<HANDLE, 2> handles = {parent, target};
    const DWORD result = WaitForMultipleObjects(
        static_cast<DWORD>(handles.size()), handles.data(), FALSE, INFINITE);
    if (result == WAIT_OBJECT_0) {
        if (!TerminateJobObject(job, ERROR_CANCELLED)) {
            return Fail(25, GetLastError());
        }
        return 0;
    }
    if (result == WAIT_OBJECT_0 + 1) {
        DWORD target_exit_code = 0;
        if (!GetExitCodeProcess(target, &target_exit_code)) {
            return Fail(25, GetLastError());
        }
        return static_cast<int>(target_exit_code);
    }
    const DWORD error =
        result == WAIT_FAILED ? GetLastError() : ERROR_INVALID_FUNCTION;
    return Fail(25, error);
}

int BrokerMain(int argc, wchar_t* argv[]) {
    if (!SetDllDirectoryW(nullptr)) {
        return Fail(11, GetLastError());
    }

    if (argc == 2 && wcscmp(argv[1], L"--version") == 0) {
        fwprintf(stdout, L"protocol=1 product=%ls\n", AACC_PRODUCT_VERSION);
        return 0;
    }

    Options options;
    if (!ParseOptions(argc, argv, &options)) {
        return Fail(10, ERROR_INVALID_PARAMETER);
    }

    EnvironmentBlock environment;
    if (!BuildSanitizedEnvironment(
            options.bundle_dir, options.codex_path, &environment)) {
        return Fail(12, GetLastError());
    }

    UniqueHandle parent(OpenProcess(SYNCHRONIZE, FALSE, options.parent_pid));
    if (!parent.IsValid()) {
        return Fail(25, GetLastError());
    }

    UniqueHandle job(CreateJobObjectW(nullptr, nullptr));
    if (!job.IsValid()) {
        return Fail(20, GetLastError());
    }
    if (!ConfigureKillOnClose(job.Get())) {
        return Fail(21, GetLastError());
    }

    TargetCommand command;
    if (!BuildCodexCommand(options.codex_path, &command)) {
        return Fail(22, GetLastError());
    }

    SuspendedProcess target;
    if (!CreateSuspendedWithStdio(command, &environment, &target)) {
        return Fail(22, GetLastError());
    }

    if (!AssignProcessToJobObject(job.Get(), target.process.Get())) {
        const DWORD error = GetLastError();
        if (TerminateProcess(target.process.Get(), kJobAssignStage)) {
            WaitForSingleObject(target.process.Get(), 5000);
        }
        return Fail(23, error);
    }

    if (ResumeThread(target.thread.Get()) == static_cast<DWORD>(-1)) {
        const DWORD error = GetLastError();
        if (TerminateJobObject(job.Get(), kResumeStage)) {
            WaitForSingleObject(target.process.Get(), 5000);
        }
        return Fail(24, error);
    }

    return WaitForParentOrTarget(parent.Get(), target.process.Get(), job.Get());
}

}  // namespace

int wmain(int argc, wchar_t* argv[]) {
    try {
        return BrokerMain(argc, argv);
    } catch (...) {
        return Fail(kEnvironmentStage, ERROR_NOT_ENOUGH_MEMORY);
    }
}
