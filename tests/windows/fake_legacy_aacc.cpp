#define UNICODE
#define _UNICODE
#include <windows.h>

#include <cwchar>

namespace {
constexpr wchar_t kClassName[] = L"AACC.LegacySmokeFixture";
constexpr wchar_t kWindowTitle[] = L"AI Agent Control Center";

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
            Sleep(60000);
        }
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

    MSG message = {};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
