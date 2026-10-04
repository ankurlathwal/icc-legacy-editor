"""Fixture (*.fxt) editing for the web editor (ICC 2000 / 2002 / 2006)."""
import datetime
import os
import shutil
from pathlib import Path

from .. import fixturefile


class SaveError(Exception):
    pass


def find_fixture_dir(*candidates):
    """First folder (or its Fxt/ subfolder, as in ICC 2006) holding *.fxt files."""
    for c in candidates:
        if c is None:
            continue
        c = Path(c)
        for d in (c, c / 'Fxt'):
            if d.is_dir() and any(d.glob('*.fxt')):
                return d
    return None


# Key slot ranges, from the comments of the game's fixture source files.
def slot_group(slot):
    if 1 <= slot <= 18:
        return 'Championship (div 1 = 1–9, div 2 = 10–18)'
    if 101 <= slot <= 128:
        return 'International rotation'
    if 221 <= slot <= 238:
        return 'National League (div 1 = 221–229, div 2 = 230–238)'
    return 'Teams'


ENGLAND = 44   # team id the game hard-codes for neutral finals (played at its 2nd ground, Lord's)

# CrTeam::isTestTeam hard-codes the Test nations as team ids 43..51 (ICC 2000/2002: 9 sides) or 43..52
# (ICC 2006 adds Bangladesh as 52). Internationals involving anyone else crash at match start
# ("an invalid argument was encountered"); Associates only appear in World Cup fixtures.
INTERNATIONAL_TYPES = {'classic': {5, 6, 7}, '2006': {5, 6, 7, 12}}


def test_nations(ff):
    return range(43, 53) if ff.fmt == '2006' else range(43, 52)


def check_international(ff, f):
    if f.type not in INTERNATIONAL_TYPES[ff.fmt]:
        return
    for slot in (f.home, f.away):
        tid = ff.team_id(slot)
        if tid is not None and tid not in test_nations(ff):
            raise ValueError('only the Test nations can play %ss in this game; '
                             'other national sides can only play in World Cup fixtures'
                             % ff.match_types[f.type][0])


def _signed(b):
    return b - 256 if b > 127 else b


def resolve_venue(ff, f, info):
    """Ground name the game will use, following CrEventList::getGround.

    ICC 2000/2002 store a team id in the fixture's ground field (play at that team's ground);
    ICC 2006 stores a ground id. Otherwise the home side's grounds are used: the Nth match of
    a Test series at its Nth ground, domestic cup finals at Lord's.
    """
    grounds, test_nations = info['team_grounds'], info['tests']
    if ff.fmt == '2006' and f.ground:
        return info['grounds'].get(f.ground, '#%d' % f.ground)
    tid = (f.ground if ff.fmt != '2006' else 0) or ff.team_id(f.home)
    if tid is None:
        return None
    lst = grounds.get(tid) or []
    rnd = _signed(f.round)
    if tid not in test_nations and rnd == 0:
        lst, idx = grounds.get(ENGLAND) or [], 1          # domestic cup final
    elif f.type == 5 and tid == ENGLAND and f.home >= 900:
        idx = 1                                            # triangular final in England
    elif f.type == 5:
        out_of = _signed(f.out_of)
        idx = int(rnd / out_of) if out_of else -1
    else:
        idx = rnd - 1
    if not 0 <= idx < len(lst):
        idx = 0
    return lst[idx] if lst else None


class FixtureSet:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.files = {p.name: fixturefile.load(p) for p in sorted(self.folder.glob('*.fxt'), key=lambda p: p.name.lower())}
        self.dirty = set()

    def get(self, name):
        if name not in self.files:
            raise KeyError('no fixture file %s' % name)
        return self.files[name]

    # ------------------------------------------------------------ listing
    def list(self):
        return [{'name': n, 'year': ff.year if 1800 < ff.year < 2100 else None,
                 'fixtures': len(ff.fixtures), 'dirty': n in self.dirty}
                for n, ff in self.files.items()]

    # set by the store: team_grounds (team id -> ground names), tests (Test nation ids), grounds (id -> name)
    venue_info = {'team_grounds': {}, 'tests': set(), 'grounds': {}}

    def fixture_json(self, ff, i, names):
        f = ff.fixtures[i]
        def team(slot):
            tid = ff.team_id(slot)
            return names.get(tid, '#%d' % tid) if tid is not None else None
        n = ff.days_of(f)
        return {
            'index': i, 'start': f.start, 'date': ff.date_of(f.start).isoformat(),
            'end': ff.date_of(f.start + n - 1).isoformat(), 'days': n,
            'type': f.type, 'competition': ff.match_types.get(f.type, ('Type %d' % f.type, 1))[0],
            'round': f.round, 'out_of': f.out_of, 'tt': f.tt, 'day_night': f.day_night,
            'home': f.home, 'away': f.away, 'home_team': team(f.home), 'away_team': team(f.away),
            'ground': f.ground, 'plays_at': resolve_venue(ff, f, self.venue_info),
        }

    def file_json(self, name, names):
        ff = self.get(name)
        slots = [{'slot': s, 'team': t, 'name': names.get(t, '#%d' % t), 'group': slot_group(s)}
                 for s, t in enumerate(ff.keys) if t not in (0xCDCD, 0xFFFF) and s > 0]
        return {
            'name': name, 'year': ff.year if 1800 < ff.year < 2100 else None,
            'season_start': ff.date_of(1).isoformat(), 'season_end': ff.date_of(fixturefile.DAYS_PER_FILE - 1).isoformat(),
            'match_types': {str(k): {'name': v[0], 'days': v[1]} for k, v in ff.match_types.items()},
            'slots': slots, 'key_size': len(ff.keys),
            # what the venue field holds: a team id (play at its ground) or, in ICC 2006, a ground id
            'venues': ([{'id': g, 'name': n} for g, n in sorted(self.venue_info['grounds'].items(), key=lambda x: x[1])]
                       if ff.fmt == '2006' else
                       [{'id': t, 'name': "%s's ground" % names.get(t, '#%d' % t)
                         + (' (%s)' % gs[0] if gs else '')}
                        for t, gs in sorted(self.venue_info['team_grounds'].items(), key=lambda x: names.get(x[0], ''))]),
            'fixtures': [self.fixture_json(ff, i, names) for i in range(len(ff.fixtures))],
        }

    # ------------------------------------------------------------ editing
    def _apply(self, ff, f, data):
        if 'date' in data:
            f.start = ff.day_of(datetime.date.fromisoformat(data['date']))
        for k in ('type', 'round', 'out_of', 'tt', 'home', 'away', 'ground'):
            if k in data:
                setattr(f, k, int(data[k]))
        if 'day_night' in data:
            f.day_night = 1 if data['day_night'] else 0
        # Slot 0 and codes past the key (850+ Super Six, 900+ cup draw positions) are
        # placeholders the game fills in as a competition progresses.
        for k in ('home', 'away'):
            slot = getattr(f, k)
            if 0 < slot < len(ff.keys) and ff.team_id(slot) is None:
                raise ValueError('key slot %d has no team' % slot)
        if f.home == f.away and ff.team_id(f.home) is not None:
            raise ValueError('a team cannot play itself')
        if f.type not in ff.match_types:
            raise ValueError('unknown competition')
        ff.check(f)
        # The game crashes starting a match it can't find a ground for (e.g. Kenya at home in ICC 2006,
        # where Kenya has no grounds), so insist on an explicit venue in that case.
        if ff.team_id(f.home) is not None and self.venue_info['team_grounds'] and \
                resolve_venue(ff, f, self.venue_info) is None:
            raise ValueError('the home side has no home ground in this database; choose a venue')

    def update(self, name, i, data, names):
        ff = self.get(name)
        f = ff.fixtures[i]
        backup = dict(vars(f))
        try:
            self._apply(ff, f, data)
            if (f.type, f.home, f.away) != (backup['type'], backup['home'], backup['away']):
                check_international(ff, f)
        except Exception:
            vars(f).update(backup)
            raise
        self.dirty.add(name)
        return self.fixture_json(ff, i, names)

    def add(self, name, data, names):
        ff = self.get(name)
        f = fixturefile.new_fixture(0, 1, 0, 0)
        self._apply(ff, f, data)
        check_international(ff, f)
        ff.fixtures.append(f)
        self.dirty.add(name)
        return self.fixture_json(ff, len(ff.fixtures) - 1, names)

    def delete(self, name, i):
        ff = self.get(name)
        del ff.fixtures[i]
        self.dirty.add(name)
        return {'deleted': i}

    def update_keys(self, name, data):
        ff = self.get(name)
        for s, t in data.items():
            s, t = int(s), int(t)
            if not 0 < s < len(ff.keys):
                raise ValueError('bad key slot %d' % s)
            if ff.keys[s] in (0xCDCD, 0xFFFF):
                raise ValueError('key slot %d is unused' % s)
            if not 0 < t < 0xCDCD:
                raise ValueError('bad team id %d' % t)
            ff.keys[s] = t
        self.dirty.add(name)
        return {'ok': True}

    # --------------------------------------------------------------- save
    def build(self):
        """Serialize every changed file and verify it in memory."""
        out = []
        for name in sorted(self.dirty):
            ff = self.files[name]
            data = fixturefile.serialize(ff)
            if fixturefile.serialize(fixturefile.parse(data)) != data:
                raise SaveError('re-read check failed for %s' % name)
            out.append((ff.path, data))
        return out

    def write(self, out, stamp):
        backups = []
        for path, data in out:
            if path.exists():
                b = path.with_name(path.name + '.bak-' + stamp)
                shutil.copy2(path, b)
                backups.append(str(b))
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, path)
        for path, data in out:
            if path.read_bytes() != data:
                raise SaveError('%s on disk does not match after saving; restore from its backup' % path.name)
        self.dirty.clear()
        return [str(p) for p, _ in out], backups
