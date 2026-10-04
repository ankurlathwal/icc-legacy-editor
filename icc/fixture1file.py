"""Fixture files of the ICC 1 engine games: International Cricket Captain (1998, eng98.fxt) and
Australian Cricket Captain (aus1998.fxt, ausX.fxt).

Reverse-engineered from each game's CrickMan.dll (`CrEventList::Serialize`, `CrDay::Serialize`,
`CrFixture::Serialize`) and CrTypes.dll (`CrMatchType`). Plain (unencrypted) MFC CArchive:

- 365 CrDay: DWORD count, then 30 object slots (CArchive::WriteObject; empty slots are null tags).
  A match is written in full on its first day. Each later day of a multi-day match has its own
  small CrFixture holding only "day n of the match" and a reference to the first-day fixture.
- CrFixture: CrMatchType (type, round, out_of; ACC adds day/night and a string, the tournament
  number of ODI Tournament matches), home, away (ACC adds a WORD ground), start day, first-day /
  last-day flags, day-in-match, then match data (object), a DWORD, the result (object) and the
  first-day fixture (object).
- 9 CrCountry blocks (28 bytes), 150 team names (CStrings), then number tables kept raw:
  4 x 6 DWORDs, the international rotation table (12 columns; 1998: 22 rows, ACC: 28),
  ACC: 5 x 5 tournament DWORDs, then a few values including the season year.

Teams are named by "slot": below 101 a position in the file's name table (ICC 1998's starts with
Derbyshire = team id 1; ACC's with an empty entry, so ACC positions are team ids); 101+ a
rotation slot; ACC 0 / 900+ = filled in by the game (knockouts).

Day 1 is 1 April (ICC 1998) or 1 October (ACC) of the season year. Unchanged files round-trip
byte-exactly: the order of fixtures inside each day is kept as the order of
`FixtureFile.fixtures`, as in fixturefile.
"""
import datetime
import heapq
from dataclasses import dataclass
from pathlib import Path

from .archive import Reader, Writer

DAYS_PER_FILE = 365
SLOTS_PER_DAY = 30
CLASS_NAME = 'CrFixture'
NONE = 0xFFFFFFFF
UNSET = 0xCDCDCDCD           # MSVC debug fill: a value the game never set

# match type -> (name, days), from CrMatchType::getMatchTypeString / getNoDays. The editor uses the
# ICC 2000 codes (fixturefile.MATCH_TYPES_CLASSIC: 6 = Test, 7 = ODI...); ICC 1998 numbers its six
# types differently, so they are translated on load and save (TYPES_1998: file code -> editor code).
TYPES_1998 = {0: 6, 1: 7, 2: 0, 3: 1, 4: 3, 5: 2}
TYPES_1998_FILE = {v: k for k, v in TYPES_1998.items()}
MATCH_TYPES_1998 = {
    0: ('County Championship', 4),
    1: ('Sunday League', 1),
    2: ('NatWest Trophy', 1),
    3: ('Benson & Hedges Cup', 1),     # "League Cup" in the game
    6: ('Test Match', 5),
    7: ('One Day International', 1),
}
MATCH_TYPES_ACC = {     # ACC's own codes already match the ICC 2000 ones
    0: ('Sheffield Shield', 4),
    1: ('Mercantile Mutual Cup', 1),
    4: ('World Cup', 1),
    5: ('ODI Tournament', 1),
    6: ('Test Match', 5),
    7: ('One Day International', 1),
    8: ('First-class Friendly', 4),
    9: ('One Day Friendly', 1),
    10: ('Club Cricket', 4),
}


@dataclass
class Fixture:
    type: int           # editor (ICC 2000) match-type code, see TYPES_1998
    round: int          # cup round (0 = final) or match number in a series; 255 = n/a (NONE in the file)
    out_of: int         # series length; 255 = n/a
    home: int           # team slot (see the module docstring)
    away: int
    start: int          # day number
    ground: int = 0     # ACC: team id whose ground is used (0 = home side's)
    day_night: int = 0  # ACC
    tt: int = 255       # ACC: tournament number of an ODI Tournament match; 255 = none
    extra: int = 0      # DWORD after the match-data object (0)
    last: int = UNSET   # first-day 'last day' flag as stored (1 for one-day matches, else unset)
    flag: int = 0


@dataclass
class FixtureFile:
    fixtures: list
    countries: list          # 9 x 28 bytes, raw
    names: list              # 150 team names
    tables: list             # remaining DWORDs, raw (rotation table at 24..)
    fmt: str                 # '1998' or 'acc'
    schema: int = 1
    path: Path = None

    @property
    def acc(self):
        return self.fmt == 'acc'

    @property
    def match_types(self):
        return MATCH_TYPES_ACC if self.acc else MATCH_TYPES_1998

    @property
    def year(self):
        # 1998: the 2nd of the last five values; ACC: the 2nd of the last four
        return self.tables[-3] if self.acc else self.tables[-4]

    # Editor options: which fixture fields this game stores.
    venue_editable = property(lambda self: self.acc)
    has_day_night = property(lambda self: self.acc)
    has_tt = property(lambda self: self.acc)

    def days_of(self, f):
        return self.match_types.get(f.type, ('?', 1))[1]

    def _day1(self):
        y = self.year if 1800 < self.year < 2100 else 1998
        return datetime.date(y, 10, 1) if self.acc else datetime.date(y, 4, 1)

    def date_of(self, day):
        return self._day1() + datetime.timedelta(days=day - 1)

    def day_of(self, date):
        return (date - self._day1()).days + 1

    # ---- teams
    @property
    def rotation_rows(self):
        return 28 if self.acc else 22

    @staticmethod
    def _rotation_index(slot):
        return 24 + (slot - 101) * 12      # first column = this file's season

    def _pos_to_id(self, pos):
        if not 0 <= pos < len(self.names) or not self.names[pos]:
            return None
        return pos if self.acc else pos + 1

    def team_id(self, slot):
        if 101 <= slot < 101 + self.rotation_rows:
            return self._pos_to_id(self.tables[self._rotation_index(slot)])
        if 0 <= slot < 101:
            return self._pos_to_id(slot)
        return None

    @property
    def keys(self):
        return [self.team_id(s) or 0xCDCD for s in range(101 + self.rotation_rows)]

    def slot_entries(self):
        """(slot, team id, group, fixed) for the editor: teams by name, then the rotation slots."""
        out = [(s, self.team_id(s), 'Teams', True) for s in range(101) if self.team_id(s)]
        out += [(s, self.team_id(s), 'International rotation (this season)', False)
                for s in range(101, 101 + self.rotation_rows) if self.team_id(s)]
        return out

    def set_slot(self, slot, team_id):
        if not 101 <= slot < 101 + self.rotation_rows or self.team_id(slot) is None:
            raise ValueError('only international rotation slots can be changed (slot %d)' % slot)
        pos = team_id if self.acc else team_id - 1
        if self._pos_to_id(pos) != team_id:
            raise ValueError("team %d is not in this fixture file's team list" % team_id)
        self.tables[self._rotation_index(slot)] = pos

    def check(self, f):
        n = self.days_of(f)
        if not 1 <= f.start or f.start + n > DAYS_PER_FILE:
            raise ValueError('a %d-day match starting on day %d does not fit in the season' % (n, f.start))
        for name in ('round', 'out_of', 'tt'):
            if not 0 <= getattr(f, name) <= 255:
                raise ValueError('%s out of range' % name)
        for d in range(f.start, f.start + n):
            if sum(1 for g in self.fixtures if g is not f and g.start <= d < g.start + self.days_of(g)) >= SLOTS_PER_DAY:
                raise ValueError('%s already has %d matches, the most the game allows on one day'
                                 % (self.date_of(d).isoformat(), SLOTS_PER_DAY))
        f.last = 1 if n == 1 else UNSET


# ------------------------------------------------------------------------- reading
class _Objects(list):
    schema = 1


def _read_object(r, acc, objects):
    tag = r.word()
    if tag == 0:
        return None
    if tag == 0xFFFF:
        objects.schema = r.word()
        name = r.raw(r.word()).decode('ascii')
        if name != CLASS_NAME:
            raise ValueError('unexpected class %s' % name)
        objects.append(CLASS_NAME)
    elif not tag & 0x8000:
        o = objects[tag] if tag < len(objects) else None
        if o is None or isinstance(o, str):
            raise ValueError('bad object reference %d' % tag)
        return o
    slot = len(objects)
    objects.append(None)
    o = _read_fixture(r, acc, objects)
    objects[slot] = o
    return o


def _read_fixture(r, acc, objects):
    if acc:
        mt = (r.dword(), r.dword(), r.dword(), r.dword(), r.string())
        home, away, ground = r.dword(), r.dword(), r.word()
    else:
        mt = (r.dword(), r.dword(), r.dword(), 0, '')
        home, away, ground = r.dword(), r.dword(), 0
    start, first, last, nth = r.dword(), r.dword(), r.dword(), r.dword()
    if _read_object(r, acc, objects) is not None:
        raise ValueError('fixture with match data at %#x' % r.pos)
    extra = r.dword()
    if _read_object(r, acc, objects) is not None:
        raise ValueError('played fixture (result present) at %#x' % r.pos)
    the = _read_object(r, acc, objects)
    if first:
        if the is not None or nth != NONE:
            raise ValueError('unexpected first-day fixture at %#x' % r.pos)
        typ = mt[0] if acc else TYPES_1998.get(mt[0])
        label_ok = mt[4] == '' or (mt[4].isdigit() and str(int(mt[4])) == mt[4] and int(mt[4]) < 255)
        if typ is None or not label_ok or not all(v == NONE or v < 255 for v in mt[1:3]):
            raise ValueError('unexpected match type at %#x' % r.pos)
        rnd, out_of = (255 if v == NONE else v for v in mt[1:3])
        return Fixture(typ, rnd, out_of, home, away, start, ground, mt[3], int(mt[4]) if mt[4] else 255,
                       extra, last)
    if not isinstance(the, Fixture) or mt[:3] != (NONE, NONE, NONE) or (home, away, start) != (NONE, NONE, UNSET) \
            or (acc and (mt[3], mt[4], ground) != (0, '', 0xFFFF)) or extra:
        raise ValueError('unexpected follow-on fixture at %#x' % r.pos)
    return ('day', the, nth, last)


def parse(data, fmt=None):
    """Parse a fixture file of either game (detected unless `fmt` is '1998' or 'acc')."""
    err = None
    for f in ([fmt] if fmt else ['1998', 'acc']):
        try:
            return _parse(data, f)
        except (ValueError, EOFError, UnicodeDecodeError) as e:
            err = e
    raise err


def _parse(data, fmt):
    acc = fmt == 'acc'
    r = Reader(data)
    objects = _Objects([None])
    days, created = [], []
    for d in range(DAYS_PER_FILE):
        n = r.dword()
        refs = []
        for _ in range(SLOTS_PER_DAY):
            o = _read_object(r, acc, objects)
            if o is None:
                continue
            if isinstance(o, Fixture):
                if o.start != d:
                    raise ValueError('fixture on day %d starts on day %d' % (d, o.start))
                created.append(o)
            refs.append(o)
        if n != len(refs):
            raise ValueError('day %d: count %d but %d fixtures' % (d, n, len(refs)))
        days.append(refs)
    countries = [r.raw(28) for _ in range(9)]
    names = [r.string() for _ in range(150)]
    ntables = (24 + 28 * 12 + 25 + 4) if acc else (24 + 22 * 12 + 5 + 4 + 5)
    if len(data) - r.pos != ntables * 4:
        raise ValueError('unrecognised fixture file tail (%d bytes)' % (len(data) - r.pos))
    tables = [r.dword() for _ in range(ntables)]
    ff = FixtureFile(_order(created, days), countries, names, tables, fmt, objects.schema)
    # every day, and every stored flag, must be exactly what serialize() regenerates
    for d, refs in enumerate(days):
        expect = []
        for f in ff.fixtures:
            n = ff.days_of(f)
            if f.start == d:
                expect.append(f)
            elif f.start < d < f.start + n:
                expect.append(('day', f, d - f.start, 1 if d == f.start + n - 1 else 0))
        if len(refs) != len(expect) or any(
                (a is not b) if isinstance(b, Fixture) else
                (isinstance(a, Fixture) or a[1] is not b[1] or a[2:] != b[2:])
                for a, b in zip(refs, expect)):
            raise ValueError("day %d does not match its fixtures' spans" % d)
    for f in ff.fixtures:
        if f.last != (1 if ff.days_of(f) == 1 else UNSET):
            raise ValueError('unexpected last-day flag')
    return ff


def _order(created, days):
    """One order of all fixtures consistent with the order inside every day."""
    idx = {id(f): i for i, f in enumerate(created)}
    succ = [set() for _ in created]
    indeg = [0] * len(created)
    for refs in days:
        seq = [x if isinstance(x, Fixture) else x[1] for x in refs]
        for a, b in zip(seq, seq[1:]):
            a, b = idx[id(a)], idx[id(b)]
            if b not in succ[a]:
                succ[a].add(b)
                indeg[b] += 1
    heap = [i for i, n in enumerate(indeg) if n == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        i = heapq.heappop(heap)
        out.append(created[i])
        for j in succ[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(heap, j)
    if len(out) != len(created):
        raise ValueError('inconsistent fixture order between days')
    return out


def load(path):
    ff = parse(Path(path).read_bytes())
    ff.path = Path(path)
    return ff


# ------------------------------------------------------------------------- writing
def serialize(ff):
    acc = ff.acc
    w = Writer()
    index = {}                       # id(first-day fixture) -> its object index
    state = {'next': 1, 'class': None}

    def tag():
        if state['class'] is None:
            w.word(0xFFFF); w.word(ff.schema); w.word(len(CLASS_NAME)); w.raw(CLASS_NAME.encode('ascii'))
            state['class'] = state['next']
            state['next'] += 1
        else:
            w.word(0x8000 | state['class'])
        state['next'] += 1
        return state['next'] - 1

    def body(mt, home, away, ground, start, first, last, nth):
        for v in mt[:3]:
            w.dword(v)
        if acc:
            w.dword(mt[3]); w.string(mt[4])
        w.dword(home); w.dword(away)
        if acc:
            w.word(ground)
        for v in (start, first, last, nth):
            w.dword(v)

    for d in range(DAYS_PER_FILE):
        todo = [(f, d - f.start, ff.days_of(f)) for f in ff.fixtures if f.start <= d < f.start + ff.days_of(f)]
        if len(todo) > SLOTS_PER_DAY:
            raise ValueError('day %d has more than %d matches' % (d, SLOTS_PER_DAY))
        w.dword(len(todo))
        for f, nth, n in todo:
            if nth == 0:
                index[id(f)] = tag()
                typ = f.type if acc else TYPES_1998_FILE[f.type]
                rnd, out_of = (NONE if v == 255 else v for v in (f.round, f.out_of))
                body((typ, rnd, out_of, f.day_night, '' if f.tt == 255 else str(f.tt)), f.home, f.away,
                     f.ground, f.start, 1, f.last, NONE)
                w.word(0); w.dword(f.extra); w.word(0); w.word(0)
            else:
                tag()
                body((NONE, NONE, NONE, 0, ''), NONE, NONE, 0xFFFF, UNSET, 0, 1 if nth == n - 1 else 0, nth)
                w.word(0); w.dword(0); w.word(0); w.word(index[id(f)])
        for _ in range(SLOTS_PER_DAY - len(todo)):
            w.word(0)
    for c in ff.countries:
        w.raw(c)
    for s in ff.names:
        w.string(s)
    for v in ff.tables:
        w.dword(v)
    return w.getvalue()


def new_fixture(ff, type_, start, home, away):
    return Fixture(type_, 255, 255, home, away, start)
