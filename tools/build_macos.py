"""Build the macOS app: dist/ICC Editor.app and dist/ICC-Editor-macOS.zip.

    python3 tools/build_macos.py

`ICC Editor.app` is a small native WebKit window (tools/macapp/main.swift) that runs the editor
(`icc/`, copied into Contents/Resources/runtime) with the Mac's own /usr/bin/python3. Needs the
Swift compiler from Apple's Command Line Tools to build; the app runs on macOS 13+ (Intel and
Apple Silicon). Rebuild after changing the editor.
"""
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
APP = DIST / 'ICC Editor.app'
SWIFT = ROOT / 'tools' / 'macapp' / 'main.swift'
MIN_MACOS = '13.0'
ICON = ROOT / 'icc' / 'editor' / 'static' / 'icon.png'          # drawn by tools/macapp/icon.swift
VERSION = re.search(r"__version__ = '([^']+)'", (ROOT / 'icc' / 'editor' / '__init__.py').read_text()).group(1)

INFO = {
    'CFBundleName': 'ICC Editor',
    'CFBundleDisplayName': 'ICC Editor',
    'CFBundleIdentifier': 'local.icc-old-db.editor',
    'CFBundleExecutable': 'ICC Editor',
    'CFBundlePackageType': 'APPL',
    'CFBundleShortVersionString': VERSION,
    'CFBundleVersion': VERSION,
    'CFBundleIconFile': 'AppIcon',
    'LSMinimumSystemVersion': MIN_MACOS,
    'NSHighResolutionCapable': True,
    'NSPrincipalClass': 'NSApplication',
    'NSHumanReadableCopyright': 'Editor for International Cricket Captain databases',
    # the window talks to the editor over http://localhost
    'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
}


def compile_binary(out):
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for arch in ('arm64', 'x86_64'):
            p = Path(tmp) / arch
            subprocess.run(['swiftc', '-O', '-swift-version', '5', '-target', '%s-apple-macos%s' % (arch, MIN_MACOS),
                            '-framework', 'Cocoa', '-framework', 'WebKit', str(SWIFT), '-o', str(p)], check=True)
            parts.append(str(p))
        subprocess.run(['lipo', '-create', *parts, '-output', str(out)], check=True)


def make_icns(out):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / 'AppIcon.iconset'
        iconset.mkdir()
        for px in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = 'icon_%dx%d%s.png' % (px, px, '@2x' if scale == 2 else '')
                subprocess.run(['sips', '-z', str(px * scale), str(px * scale), str(ICON), '--out', str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(out)], check=True)


def main():
    if APP.exists():
        shutil.rmtree(APP)
    macos = APP / 'Contents' / 'MacOS'
    res = APP / 'Contents' / 'Resources'
    macos.mkdir(parents=True)
    res.mkdir()
    compile_binary(macos / 'ICC Editor')
    (APP / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(INFO))
    make_icns(res / 'AppIcon.icns')
    for p in sorted((ROOT / 'icc').rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.html', '.png'):
            dest = res / 'runtime' / p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(APP)], check=True)   # ad-hoc signature
    out = DIST / 'ICC-Editor-macOS.zip'
    if out.exists():
        out.unlink()
    subprocess.run(['ditto', '-c', '-k', '--keepParent', str(APP), str(out)], check=True)
    print('built %s and %s (%.2f MB)' % (APP, out, out.stat().st_size / 1e6))
    return 0


if __name__ == '__main__':
    sys.exit(main())
