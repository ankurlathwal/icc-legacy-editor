"""Drop-in replacement for convdb.exe:

    python -m icc.convdb [-d|-e] fileIn fileOut [--game 2005|2006]

ICC 2005 and 2006 seed their encryption differently. When decrypting, the variant is detected
automatically; when encrypting an ICC 2005 / 2006 file, pass --game 2005 or --game 2006.
"""
import sys

from .crypto import read_db, write_db


def _detect_2006(path):
    """True if the encrypted file only makes sense with the ICC 2005 / 2006 seed."""
    from . import playerfile, teamfile
    for seed in (False, True):
        data = read_db(path, seed)
        fmts = [f for f in playerfile.FORMATS if f.seed_with_length == seed]
        for parse in [lambda d, f=f: playerfile.parse(d, f) for f in fmts] + [teamfile.parse]:
            try:
                parse(data)
                return seed
            except Exception:
                pass
    raise ValueError('%s is not a recognised ICC player or team file' % path)


def main(argv):
    game2006 = '--game' in argv and argv[argv.index('--game') + 1] in ('2005', '2006')
    argv = [a for i, a in enumerate(argv) if a != '--game' and (i == 0 or argv[i - 1] != '--game')]
    if len(argv) != 3 or argv[0] not in ('-d', '-e'):
        print('Usage: python -m icc.convdb [-d|-e] fileIn fileOut [--game 2005|2006]')
        return 1
    mode, src, dst = argv
    if mode == '-d':
        seed = game2006 or _detect_2006(src)
        with open(dst, 'wb') as f:
            f.write(read_db(src, seed))
    else:
        with open(src, 'rb') as f:
            write_db(dst, f.read(), game2006)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
