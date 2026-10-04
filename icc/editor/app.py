"""Desktop-app support: run the editor in its own Chrome/Edge "app mode" window.

- recent game folders and the app's browser profile live in a per-user config folder;
- folders are chosen with an in-app browser (`browse`), or the macOS app's native panel;
- the window is a Chromium browser started with --app and a private profile, so it is its own
  process: when the user closes the window, that process ends and the editor shuts down.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RECENT_MAX = 8


def config_dir():
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home())
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    d = base / 'ICC Editor'
    d.mkdir(parents=True, exist_ok=True)
    return d


def recent_folders():
    try:
        items = json.loads((config_dir() / 'recent.json').read_text())
    except (OSError, ValueError):
        return []
    return [p for p in items if isinstance(p, str) and Path(p).is_dir()]


def remember_folder(path):
    path = str(Path(path).resolve())
    items = [path] + [p for p in recent_folders() if p != path]
    try:
        (config_dir() / 'recent.json').write_text(json.dumps(items[:RECENT_MAX], indent=1))
    except OSError:
        pass


def _hidden():
    """subprocess flags that keep a console window from flashing up on Windows."""
    return {'creationflags': 0x08000000} if sys.platform == 'win32' else {}


def looks_like_game(folder):
    """Cheap check (no parsing) for an ICC database folder: dataT.db (+ Data\\ for ICC 2006) or database.db."""
    for d in (folder, folder / 'Data'):
        try:
            names = {n.lower() for n in os.listdir(d)}
        except OSError:
            continue
        if ('datat.db' in names and 'datap.db' in names) or 'database.db' in names:
            return True
    return False


def roots():
    """Starting points for the in-app folder browser."""
    out = []
    home = Path.home()
    for label, p in (('Home', home), ('Desktop', home / 'Desktop'), ('Documents', home / 'Documents')):
        if p.is_dir():
            out.append({'name': label, 'path': str(p)})
    if sys.platform == 'win32':
        drives = [d for d in ('%s:\\' % chr(c) for c in range(ord('A'), ord('Z') + 1)) if os.path.isdir(d)]
        out += [{'name': d[:2], 'path': d} for d in drives]
    elif sys.platform == 'darwin':
        vols = Path('/Volumes')
        out += [{'name': v.name, 'path': str(v)} for v in sorted(vols.iterdir()) if v.is_dir()] if vols.is_dir() else []
    else:
        out.append({'name': '/', 'path': '/'})
    return out


def browse(path):
    """List the sub-folders of `path` for the in-app folder browser, marking ICC game folders.

    Used instead of a native folder dialog on Windows, where a dialog opened by this background
    process would appear behind the editor window.
    """
    if not path:
        return {'path': None, 'parent': None, 'game': False, 'folders': [], 'roots': roots()}
    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise ValueError('not a folder: %s' % path)
    folder = folder.resolve()
    folders = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError as e:
        raise ValueError('cannot open %s: %s' % (folder, e.strerror or e))
    for e in entries:
        if e.name.startswith(('.', '$')) or e.name in ('System Volume Information',):
            continue
        try:
            if not e.is_dir():
                continue
        except OSError:
            continue
        sub = Path(e.path)
        folders.append({'name': e.name, 'path': str(sub), 'game': looks_like_game(sub)})
    parent = folder.parent if folder.parent != folder else None
    return {'path': str(folder), 'parent': str(parent) if parent else None, 'game': looks_like_game(folder),
            'folders': folders, 'roots': roots()}


def find_browser():
    """A Chromium-based browser that supports --app windows, or None."""
    if sys.platform == 'win32':
        roots = [os.environ.get(k) for k in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')]
        rels = [r'Microsoft\Edge\Application\msedge.exe', r'Google\Chrome\Application\chrome.exe',
                r'BraveSoftware\Brave-Browser\Application\brave.exe', r'Vivaldi\Application\vivaldi.exe']
        cands = [Path(r) / rel for rel in rels for r in roots if r]
    elif sys.platform == 'darwin':
        apps = ['Google Chrome.app/Contents/MacOS/Google Chrome',
                'Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
                'Brave Browser.app/Contents/MacOS/Brave Browser',
                'Chromium.app/Contents/MacOS/Chromium', 'Vivaldi.app/Contents/MacOS/Vivaldi']
        cands = [Path(base) / a for a in apps for base in ('/Applications', Path.home() / 'Applications')]
    else:
        cands = [Path(p) for p in (shutil.which(n) for n in ('google-chrome', 'chromium', 'chromium-browser',
                                                             'microsoft-edge', 'vivaldi')) if p]
    return next((c for c in cands if c.is_file()), None)


def open_window(url):
    """Open the editor in an app-mode window. Returns the browser process, or None if no
    suitable browser was found (the caller then falls back to a normal browser tab)."""
    exe = find_browser()
    if exe is None:
        return None
    profile = config_dir() / 'window'
    args = [str(exe), '--app=' + url, '--user-data-dir=' + str(profile), '--no-first-run',
            '--no-default-browser-check', '--disable-features=Translate', '--window-size=1400,900']
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_hidden())


def show_error(message):
    """Report a startup problem when there is no console to print it to."""
    print(message, file=sys.stderr)
    try:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'ICC Editor', 0x10)
        elif sys.platform == 'darwin':
            subprocess.run(['osascript', '-e', 'display alert "ICC Editor" message (system attribute "ICC_MSG") as critical'],
                           env=dict(os.environ, ICC_MSG=message), capture_output=True)
    except Exception:
        pass
