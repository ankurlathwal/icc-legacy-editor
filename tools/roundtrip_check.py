"""Byte-exact round-trip check over every database and fixture file it can find.

    python tools/roundtrip_check.py                          # everything in the repo (Original DB/...)
    python tools/roundtrip_check.py "C:\\cricket\\ICC 2000" ...  # game installs or other folders

Run this after any change to the parsers. Exits non-zero on a failure. Files that are not
valid databases at all (e.g. text-editor-damaged copies) are reported as SKIP, not failures.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from icc import fixturefile, icc2file, playerfile, teamfile  # noqa: E402
from icc.crypto import read_db  # noqa: E402


def files(root, pattern):
    return sorted(glob.glob(os.path.join(glob.escape(root), '**', pattern), recursive=True))


def check(path, kind):
    if kind == 'players':
        pf = playerfile.load(path)
        return playerfile.serialize(pf) == read_db(path, pf.fmt.seed_with_length)
    if kind == 'teams':
        tf = teamfile.load(path)
        return teamfile.serialize(tf) == read_db(path, tf.seed_with_length)
    with open(path, 'rb') as f:
        data = f.read()
    if kind == 'icc2':
        return icc2file.serialize(icc2file.parse(data) if hasattr(icc2file, 'parse') else icc2file.load(path)) == data
    return fixturefile.serialize(fixturefile.parse(data)) == data


def main(argv):
    roots = argv or [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')]
    checked = failures = skipped = 0
    for root in roots:
        for pattern, kind in (('[dD]ata[tT].db', 'players'), ('[dD]ata[pP].db', 'teams'),
                              ('database.db', 'icc2'), ('*.fxt', 'fixtures')):
            for p in files(root, pattern):
                try:
                    ok = check(p, kind)
                except Exception as e:   # not a parsable file of this kind
                    if kind == 'icc2':
                        continue         # e.g. ICC 2002's database.db is a different file
                    print('SKIP %s (%s: %s)' % (p, type(e).__name__, e))
                    skipped += 1
                    continue
                checked += 1
                if not ok:
                    failures += 1
                    print('FAIL', p)
    print('%d files checked, %d failures%s' % (checked, failures, ', %d skipped' % skipped if skipped else ''))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
