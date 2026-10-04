"""Build a self-contained Windows package of the editor (no Python install needed).

    python3 tools/build_windows.py            # -> dist/ICC-Editor-Windows.zip

The zip holds `ICC Editor.exe` (a small launcher, tools/winlauncher/launcher.c, compiled with Zig:
`winget install zig.zig` on Windows, `brew install zig` on a Mac), and a `runtime` folder with the official embeddable Python from
python.org (downloaded once into dist/.cache), the `icc` package and `Troubleshoot.bat`. The exe
starts the editor as a desktop app in an Edge app window, with no console. Rebuild after changing
the editor.
"""
import io
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PY_VERSION = '3.13.16'
PY_URL = 'https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip' % (PY_VERSION, PY_VERSION)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
NAME = 'ICC Editor'
LAUNCHER_DIR = ROOT / 'tools' / 'winlauncher'
ICON = ROOT / 'icc' / 'editor' / 'static' / 'icon.png'
VERSION = re.search(r"__version__ = '([^']+)'", (ROOT / 'icc' / 'editor' / '__init__.py').read_text()).group(1)

# The editor with a console window, for seeing error messages (lives in runtime\).
CONSOLE = r'''@echo off
rem Starts the ICC editor with a console window, so any error message stays visible.
cd /d "%~dp0"
if "%~1"=="" (python.exe -m icc.editor --app) else (python.exe -m icc.editor --app --game-dir "%~1")
pause
'''

README = r'''ICC Editor for Windows
======================

1. Unzip this folder anywhere (e.g. your Desktop). No install is needed.
2. Double-click "ICC Editor.exe". The editor opens in its own window (Microsoft Edge app mode).
   Keep ICC Editor.exe next to the "runtime" folder; it needs it.
3. Click "Choose game folder..." and pick a game folder, e.g. C:\Games\ICC 2000.
   Recent folders are listed for next time; "Open another game" switches game.
4. Edit, then press "Save to game files". Closing the window (or Quit) closes the editor.

The Help button in the editor has a full guide.

- First run: Windows SmartScreen may say "Windows protected your PC", because the exe is not
  signed by a registered publisher. Click "More info" then "Run anyway".
- Every save first keeps a copy of each changed file as <name>.bak-<date-time> next to it.
- Close the game before saving; it reads the databases when it starts a new game.
- If the game is installed under "Program Files", Windows may block saving there.
  Move the game to a folder such as C:\Games.
- Desktop shortcut: right-click "ICC Editor.exe" > Send to > Desktop (create shortcut).
- If nothing appears, run runtime\Troubleshoot.bat to see the error message.
  A log is kept in %APPDATA%\ICC Editor\editor.log.
'''


def embeddable_python():
    cache = DIST / '.cache' / Path(PY_URL).name
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print('downloading', PY_URL)
        with urllib.request.urlopen(PY_URL) as r:
            cache.write_bytes(r.read())
    return cache.read_bytes()


ICO = ROOT / 'icc' / 'editor' / 'static' / 'icon.ico'   # committed; regenerate on a Mac with make_ico()


def make_ico():
    """Windows icon (PNG-compressed entries) from icc/editor/static/icon.png, resized with macOS `sips`.
    Only needed when the icon changes:  python3 -c "import tools.build_windows as b; ..."  (see CLAUDE.md)."""
    sizes = (16, 24, 32, 48, 64, 128, 256)
    pngs = []
    with tempfile.TemporaryDirectory() as tmp:
        for px in sizes:
            out = Path(tmp) / ('%d.png' % px)
            subprocess.run(['sips', '-z', str(px), str(px), str(ICON), '--out', str(out)], check=True, capture_output=True)
            pngs.append(out.read_bytes())
    head = struct.pack('<HHH', 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b''
    for px, data in zip(sizes, pngs):
        entries += struct.pack('<BBBBHHII', px % 256, px % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return head + entries + b''.join(pngs)


def build_exe():
    """Cross-compile ICC Editor.exe (icon, version info and manifest embedded)."""
    zig = shutil.which('zig')
    if not zig:
        raise SystemExit('Zig is needed to build ICC Editor.exe: winget install zig.zig (Windows) or brew install zig (Mac)')
    nums = (VERSION.split('.') + ['0', '0', '0'])[:4]
    rc = '''1 ICON "icon.ico"
1 24 "launcher.manifest"
1 VERSIONINFO
FILEVERSION {n}
PRODUCTVERSION {n}
FILEOS 0x40004
FILETYPE 0x1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "ICC community"
      VALUE "FileDescription", "ICC Editor"
      VALUE "FileVersion", "{v}"
      VALUE "InternalName", "ICC Editor"
      VALUE "LegalCopyright", "Unofficial fan-made editor for International Cricket Captain"
      VALUE "OriginalFilename", "ICC Editor.exe"
      VALUE "ProductName", "ICC Editor"
      VALUE "ProductVersion", "{v}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
'''.format(n=','.join(nums), v=VERSION)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'icon.ico').write_bytes(ICO.read_bytes())
        shutil.copy(LAUNCHER_DIR / 'launcher.c', t / 'launcher.c')
        shutil.copy(LAUNCHER_DIR / 'launcher.manifest', t / 'launcher.manifest')
        (t / 'launcher.rc').write_text(rc)
        subprocess.run([zig, 'cc', '-target', 'x86_64-windows-gnu', '-Os', '-s', '-municode', '-Wl,--subsystem,windows',
                        'launcher.c', 'launcher.rc', '-lshell32', '-o', 'launcher.exe'], cwd=t, check=True)
        return (t / 'launcher.exe').read_bytes()


def crlf(text):
    return text.replace('\n', '\r\n')


def main():
    out = DIST / 'ICC-Editor-Windows.zip'
    DIST.mkdir(exist_ok=True)
    exe = build_exe()
    py = zipfile.ZipFile(io.BytesIO(embeddable_python()))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('%s/ICC Editor.exe' % NAME, exe)
        z.writestr('%s/README.txt' % NAME, crlf(README))
        for info in py.infolist():
            z.writestr('%s/runtime/%s' % (NAME, info.filename), py.read(info))
        for p in sorted((ROOT / 'icc').rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.html', '.png'):
                z.write(p, '%s/runtime/%s' % (NAME, p.relative_to(ROOT).as_posix()))
        z.writestr('%s/runtime/Troubleshoot.bat' % NAME, crlf(CONSOLE))
    print('built %s (%.1f MB; ICC Editor.exe %d KB)' % (out, out.stat().st_size / 1e6, len(exe) // 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
