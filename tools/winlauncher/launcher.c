/* ICC Editor.exe - Windows launcher for the bundled editor.
 *
 * Starts runtime\pythonw.exe -m icc.editor --app (no console window) from the folder this exe
 * is in; the editor then opens in its own Edge app window. Dropping a game folder onto the exe
 * passes it on as --game-dir. Cross-compiled on macOS by tools/build_windows.py with Zig.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

static void fail(const wchar_t *msg) {
    MessageBoxW(NULL, msg, L"ICC Editor", MB_ICONERROR | MB_OK);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmd, int show) {
    wchar_t dir[MAX_PATH], runtime[MAX_PATH], python[MAX_PATH], line[3 * MAX_PATH + 64], folder[MAX_PATH];
    (void)inst; (void)prev; (void)cmd; (void)show;

    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { fail(L"Could not find where ICC Editor is installed."); return 1; }
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    swprintf(runtime, MAX_PATH, L"%ls\\runtime", dir);
    swprintf(python, MAX_PATH, L"%ls\\pythonw.exe", runtime);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        fail(L"The \"runtime\" folder is missing.\n\nUnzip the whole ICC Editor folder first, and keep "
             L"ICC Editor.exe next to its runtime folder.");
        return 1;
    }

    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv && argc > 1) {            /* a folder dropped onto the exe */
        wcsncpy(folder, argv[1], MAX_PATH - 1);
        folder[MAX_PATH - 1] = 0;
        size_t len = wcslen(folder);
        while (len > 3 && folder[len - 1] == L'\\') folder[--len] = 0;   /* "C:\x\" would escape the quote */
        swprintf(line, sizeof line / sizeof *line, L"\"%ls\" -m icc.editor --app --game-dir \"%ls\"", python, folder);
    } else {
        swprintf(line, sizeof line / sizeof *line, L"\"%ls\" -m icc.editor --app", python);
    }
    if (argv) LocalFree(argv);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessW(python, line, NULL, NULL, FALSE, 0, NULL, runtime, &si, &pi)) {
        wchar_t msg[256];
        swprintf(msg, 256, L"Could not start the editor (Windows error %lu).\n\nTry runtime\\Troubleshoot.bat to see what went wrong.",
                 (unsigned long)GetLastError());
        fail(msg);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
