#include <windows.h>

#include <cstdio>
#include <vector>

namespace {
bool WriteMarker(const wchar_t* path, const char* marker, DWORD marker_size) {
    HANDLE file = CreateFileW(
        path,
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    DWORD written = 0;
    const BOOL wrote = WriteFile(file, marker, marker_size, &written, nullptr);
    CloseHandle(file);
    return wrote && written == marker_size;
}

bool ReadAllBytes(const wchar_t* path, std::vector<unsigned char>* result) {
    HANDLE file = CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    LARGE_INTEGER size = {};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
        size.QuadPart > static_cast<LONGLONG>(MAXDWORD)) {
        CloseHandle(file);
        return false;
    }
    result->resize(static_cast<size_t>(size.QuadPart));
    DWORD read = 0;
    const BOOL read_ok =
        ReadFile(file, result->data(), static_cast<DWORD>(result->size()), &read, nullptr);
    CloseHandle(file);
    return read_ok && read == static_cast<DWORD>(result->size());
}
}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc != 3 && argc != 6) {
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
    const char ready_marker[] = "LOCK_READY\n";
    if (!WriteMarker(argv[2], ready_marker, sizeof(ready_marker) - 1)) {
        CloseHandle(payload);
        return 66;
    }
    std::puts("LOCK_READY");
    std::fflush(stdout);

    if (argc == 6) {
        std::vector<unsigned char> packaged_bytes;
        if (!ReadAllBytes(argv[4], &packaged_bytes)) {
            CloseHandle(payload);
            return 67;
        }
        while (true) {
            std::vector<unsigned char> installed_bytes;
            if (ReadAllBytes(argv[3], &installed_bytes) &&
                installed_bytes == packaged_bytes) {
                const char observed_marker[] = "ROLLBACK_PROBE_OBSERVED\n";
                if (!WriteMarker(
                        argv[5], observed_marker, sizeof(observed_marker) - 1)) {
                    CloseHandle(payload);
                    return 68;
                }
                std::puts("ROLLBACK_PROBE_OBSERVED");
                std::fflush(stdout);
                break;
            }
            Sleep(1);
        }
    }

    Sleep(INFINITE);
    CloseHandle(payload);
    return 0;
}
