#define UNICODE
#define _UNICODE
#include <windows.h>

#include <cstdio>

int wmain(int argc, wchar_t** argv) {
    if (argc != 3) {
        return 64;
    }
    HANDLE payload = CreateFileW(
        argv[1],
        GENERIC_READ,
        0,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (payload == INVALID_HANDLE_VALUE) {
        return 65;
    }
    HANDLE ready = CreateFileW(
        argv[2],
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (ready == INVALID_HANDLE_VALUE) {
        CloseHandle(payload);
        return 66;
    }
    const char marker[] = "LOCK_READY\n";
    DWORD written = 0;
    const BOOL wrote = WriteFile(ready, marker, sizeof(marker) - 1, &written, nullptr);
    CloseHandle(ready);
    if (!wrote || written != sizeof(marker) - 1) {
        CloseHandle(payload);
        return 67;
    }
    std::puts("LOCK_READY");
    std::fflush(stdout);
    Sleep(INFINITE);
    CloseHandle(payload);
    return 0;
}
