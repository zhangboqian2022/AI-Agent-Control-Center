#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdlib>
#include <cwchar>
#include <iostream>
#include <string>
#include <vector>

std::wstring EnvironmentValue(const wchar_t* name) {
    const DWORD required = GetEnvironmentVariableW(name, nullptr, 0);
    if (required == 0) {
        return {};
    }
    std::vector<wchar_t> buffer(required, L'\0');
    const DWORD length =
        GetEnvironmentVariableW(name, buffer.data(), required);
    if (length == 0 || length >= required) {
        return {};
    }
    return std::wstring(buffer.data(), length);
}

bool PathHasEntry(
    const std::wstring& path,
    const std::wstring& expected,
    bool include_children) {
    size_t start = 0;
    while (start <= path.size()) {
        const size_t separator = path.find(L';', start);
        const size_t length =
            separator == std::wstring::npos ? path.size() - start : separator - start;
        const std::wstring entry = path.substr(start, length);
        if (entry == expected) {
            return true;
        }
        if (include_children && entry.size() > expected.size() &&
            entry.compare(0, expected.size(), expected) == 0 &&
            (entry[expected.size()] == L'\\' || entry[expected.size()] == L'/')) {
            return true;
        }
        if (separator == std::wstring::npos) {
            break;
        }
        start = separator + 1;
    }
    return false;
}

int wmain(int argc, wchar_t* argv[]) {
    if (argc != 3 || std::wstring(argv[1]) != L"app-server" ||
        std::wstring(argv[2]) != L"--stdio") {
        return 90;
    }

    std::string request;
    if (!std::getline(std::cin, request)) {
        return 91;
    }

    const std::wstring path = EnvironmentValue(L"PATH");
    const std::wstring bundle = EnvironmentValue(L"AACC_TEST_BUNDLE_DIR");
    const std::wstring preserved =
        EnvironmentValue(L"AACC_TEST_PRESERVED_PATH_ENTRY");
    const std::wstring broker_target =
        EnvironmentValue(L"AACC_BROKER_CODEX_TARGET");
    const std::wstring expected_target =
        EnvironmentValue(L"AACC_TEST_EXPECTED_CODEX_TARGET");
    const bool bundle_in_path =
        !bundle.empty() && PathHasEntry(path, bundle, true);
    const bool preserved_path_present =
        !preserved.empty() && PathHasEntry(path, preserved, false);
    const bool broker_target_matches_expected =
        !expected_target.empty() && broker_target == expected_target;

    std::cout << "{\"pid\":" << GetCurrentProcessId()
              << ",\"args\":[\"app-server\",\"--stdio\"],\"request\":"
              << request << ",\"bundle_in_path\":"
              << (bundle_in_path ? "true" : "false")
              << ",\"preserved_path_present\":"
              << (preserved_path_present ? "true" : "false")
              << ",\"broker_target_matches_expected\":"
              << (broker_target_matches_expected ? "true" : "false")
              << "}" << std::endl;

    wchar_t exit_code[16] = {};
    const DWORD length =
        GetEnvironmentVariableW(L"AACC_TEST_EXIT_CODE", exit_code, 16);
    if (length == 0 || length >= 16) {
        return 0;
    }
    wchar_t* end = nullptr;
    const unsigned long parsed = wcstoul(exit_code, &end, 10);
    if (end == exit_code || *end != L'\0' || parsed > 255) {
        return 93;
    }
    return static_cast<int>(parsed);
}
