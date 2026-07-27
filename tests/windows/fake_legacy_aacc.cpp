#include <windows.h>

#include <cwchar>
#include <string>
#include <vector>

namespace {
constexpr wchar_t kClassName[] = L"AACC.LegacySmokeFixture";
constexpr wchar_t kWindowTitle[] = L"AI Agent Control Center";

std::wstring EscapeJson(const wchar_t* value) {
    std::wstring escaped;
    for (const wchar_t* current = value; current != nullptr && *current != L'\0'; ++current) {
        if (*current == L'\\' || *current == L'"') {
            escaped.push_back(L'\\');
        }
        escaped.push_back(*current);
    }
    return escaped;
}

void RecordControlIdentity(const wchar_t* mode) {
    wchar_t evidence_path[MAX_PATH * 4] = {};
    if (GetEnvironmentVariableW(
            L"AACC_LEGACY_EVIDENCE_FILE", evidence_path, MAX_PATH * 4) == 0) {
        return;
    }
    wchar_t image_path[MAX_PATH * 4] = {};
    if (GetModuleFileNameW(nullptr, image_path, MAX_PATH * 4) == 0) {
        return;
    }
    FILETIME creation = {};
    FILETIME exit_time = {};
    FILETIME kernel = {};
    FILETIME user = {};
    if (!GetProcessTimes(GetCurrentProcess(), &creation, &exit_time, &kernel, &user)) {
        return;
    }
    ULARGE_INTEGER creation_value = {};
    creation_value.LowPart = creation.dwLowDateTime;
    creation_value.HighPart = creation.dwHighDateTime;
    const std::wstring json =
        L"{\"pid\":" + std::to_wstring(GetCurrentProcessId()) +
        L",\"image_path\":\"" + EscapeJson(image_path) +
        L"\",\"creation_time\":" + std::to_wstring(creation_value.QuadPart) +
        L",\"mode\":\"" + EscapeJson(mode) + L"\"}\n";
    const int byte_count = WideCharToMultiByte(
        CP_UTF8, 0, json.c_str(), static_cast<int>(json.size()), nullptr, 0, nullptr, nullptr);
    if (byte_count <= 0) {
        return;
    }
    std::vector<char> bytes(static_cast<size_t>(byte_count));
    if (WideCharToMultiByte(
            CP_UTF8,
            0,
            json.c_str(),
            static_cast<int>(json.size()),
            bytes.data(),
            byte_count,
            nullptr,
            nullptr) != byte_count) {
        return;
    }
    HANDLE file = CreateFileW(
        evidence_path,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    DWORD written = 0;
    WriteFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr);
    CloseHandle(file);
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) {
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR command_line, int) {
    if (command_line != nullptr && wcsstr(command_line, L"--shutdown-for-update") != nullptr) {
        wchar_t mode[16] = {};
        if (GetEnvironmentVariableW(L"AACC_LEGACY_CONTROL_MODE", mode, 16) > 0 &&
            _wcsicmp(mode, L"timeout") == 0) {
            RecordControlIdentity(mode);
            Sleep(60000);
            return 73;
        }
        RecordControlIdentity(mode);
        if (_wcsicmp(mode, L"false-success") == 0) {
            return 0;
        }
        return 73;
    }

    WNDCLASSW window_class = {};
    window_class.lpfnWndProc = WindowProcedure;
    window_class.hInstance = instance;
    window_class.lpszClassName = kClassName;
    if (RegisterClassW(&window_class) == 0) {
        return 70;
    }
    HWND window = CreateWindowExW(
        0,
        kClassName,
        kWindowTitle,
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        320,
        200,
        nullptr,
        nullptr,
        instance,
        nullptr);
    if (window == nullptr) {
        return 71;
    }
    ShowWindow(window, SW_HIDE);

    wchar_t stop_event_name[256] = {};
    constexpr DWORD stop_event_name_capacity =
        static_cast<DWORD>(sizeof(stop_event_name) / sizeof(stop_event_name[0]));
    const DWORD stop_event_name_length = GetEnvironmentVariableW(
        L"AACC_LEGACY_STOP_EVENT",
        stop_event_name,
        stop_event_name_capacity);
    if (stop_event_name_length == 0 ||
        stop_event_name_length >= stop_event_name_capacity) {
        DestroyWindow(window);
        return 72;
    }
    HANDLE stop_event = OpenEventW(SYNCHRONIZE, FALSE, stop_event_name);
    if (stop_event == nullptr) {
        DestroyWindow(window);
        return 72;
    }

    MSG message = {};
    bool running = true;
    while (running) {
        const DWORD wait_result = MsgWaitForMultipleObjects(
            1,
            &stop_event,
            FALSE,
            INFINITE,
            QS_ALLINPUT);
        if (wait_result == WAIT_OBJECT_0) {
            break;
        }
        if (wait_result != WAIT_OBJECT_0 + 1) {
            CloseHandle(stop_event);
            DestroyWindow(window);
            return 72;
        }
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != FALSE) {
            if (message.message == WM_QUIT) {
                running = false;
                break;
            }
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    CloseHandle(stop_event);
    if (IsWindow(window) != FALSE) {
        DestroyWindow(window);
    }
    return 0;
}
