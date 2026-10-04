"""Fixture files (*.fxt) of ICC 2000 / 2002 / 2006.

Reverse-engineered from CrickMan.dll (`CrEventList::serializeFixtures`, `CrDay::Serialize`,
`CrFixture::Serialize`, `CrResult::Serialize`) and CrTypes.dll (`CrMatchType`). The files are
plain (unencrypted) MFC CArchive streams:

- 365 CrDay: WORD day number, DWORD count, then `count` CrFixture objects. A fixture is
  written in full the first time and as an object reference on each later day it spans
  (4 days for a County Championship match, 5 for a Test...).
- the fixture key: 300 WORDs (305 in ICC 2006) mapping a key slot to a team id of the team
  file. Fixtures name their teams by key slot, not by team id. Slots 1-18 are the counties
  in championship-division order, 221-238 the National League divisions, 101-128
  international rotation slots; 0xCDCD marks an unused slot.
- 18 DWORDs (county order), 28x12 DWORDs (international rotation: slot 101+row in each of
  12 years), 5x5 DWORDs (triangular series: key, three teams, ...), then a few trailing values
  (ICC 2000/2002: year + one DWORD; ICC 2006: 6 DWORDs + 3 WORDs).

Day 0 is 31 March of the season's year, so day 1 = 1 April and day 270 = 26 December.
Team values of 850+ / 900+ in a fixture are knockout placeholders (e.g. World Cup
Super Six / semi-final winners).

The order in which fixtures are listed inside each day is the order the game's fixture
compiler added them; it is kept as the order of `FixtureFile.fixtures` (a single order
consistent with every day, recovered on load), so unchanged files round-trip byte-exactly.
"""
import datetime
import heapq
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .archive import Reader, Writer

DAYS_PER_FILE = 365
CLASS_NAME = 'CrFixture'

# match type -> (name, days). From CrMatchType::getMatchTypeString / getNoDays.
MATCH_TYPES_CLASSIC = {
    0: ('County Championship', 4),
    1: ('National League', 1),
    2: ('NatWest Trophy', 1),
    3: ('Benson & Hedges Cup', 1),
    4: ('World Cup', 1),
    5: ('ODI Tournament', 1),
    6: ('Test Match', 5),
    7: ('One Day International', 1),
    8: ('Four Day Friendly', 4),
    9: ('One Day Friendly', 1),
    10: ('Second XI', 4),
    11: ('University Match', 3),
    12: ('One Day Match', 1),
    13: ('First Class Match', 4),
}
MATCH_TYPES_2006 = {
    0: ('County Championship', 4),
    1: ('Pro40 League', 1),
    2: ('Challenge Trophy', 1),
    3: ('Twenty20 Cup', 1),
    4: ('World Cup', 1),
    5: ('ODI Tournament', 1),
    6: ('Test Match', 5),
    7: ('One Day International', 1),
    8: ('Three Day Friendly', 3),
    9: ('One Day Friendly', 1),
    10: ('Second XI', 4),
    11: ('University Match', 3),
    12: ('International T20', 1),
    13: ('One Day Match', 1),
    14: ('First Class Match', 4),
}

# Result block of an unplayed fixture (status word, 4 null objects, uninitialised fields).
BLANK_RESULT = bytes.fromhex('3477' + '0000' * 4 + 'cd' * 8 + '00' + 'cd' * 4)


@dataclass
class Fixture:
    type: int           # match type (see MATCH_TYPES_*)
    round: int          # league: division (101, 102); cup: round (0 = final); Test/ODI: match number
    out_of: int         # series length (Tests/ODIs); 255 = n/a
    day_night: int
    tt: int             # triangular tournament number; 255 = n/a
    result: bytes       # CrResult, kept raw (fixture files are unplayed)
    home: int           # fixture-key slot (or 850+/900+ placeholder)
    away: int
    ground: int         # team id whose home ground is used; 0 = home side's ground
    start: int          # day number (0 = 31 March)
    flag: int


@dataclass
class FixtureFile:
    fixtures: list
    keys: list               # WORDs: key slot -> team id
    county_order: list       # 18 DWORDs
    rotation: list           # 28 x 12 DWORDs
    tournaments: list        # 5 x 5 DWORDs
    tail: bytes              # trailing values, kept raw
    fmt: str                 # 'classic' (2000/2002) or '2006'
    schema: int = 1
    path: Path = None

    @property
    def match_types(self):
        return MATCH_TYPES_2006 if self.fmt == '2006' else MATCH_TYPES_CLASSIC

    @property
    def year(self):
        return struct.unpack_from('<I', self.tail, 0)[0]

    def days_of(self, f):
        return self.match_types.get(f.type, ('?', 1))[1]

    def date_of(self, day):
        y = self.year if 1800 < self.year < 2100 else 2000
        return datetime.date(y, 3, 31) + datetime.timedelta(days=day)

    def day_of(self, date):
        y = self.year if 1800 < self.year < 2100 else 2000
        return (date - datetime.date(y, 3, 31)).days

    def team_id(self, slot):
        if 0 <= slot < len(self.keys) and self.keys[slot] != 0xCDCD:
            return self.keys[slot]
        return None

    def check(self, f):
        n = self.days_of(f)
        if not 0 <= f.start or f.start + n > DAYS_PER_FILE:
            raise ValueError('a %d-day match starting on day %d does not fit in the season' % (n, f.start))
        for name in ('type', 'round', 'out_of', 'day_night', 'tt', 'flag'):
            if not 0 <= getattr(f, name) <= 255:
                raise ValueError('%s out of range' % name)
        for name in ('home', 'away', 'ground'):
            if not 0 <= getattr(f, name) <= 0xFFFF:
                raise ValueError('%s out of range' % name)


# ------------------------------------------------------------------------- reading
def _read_fixture(r):
    mt = r.raw(5)
    rs = r.pos
    r.word()
    for _ in range(4):
        if r.word() != 0:
            raise ValueError('played fixture (result objects present) at %#x' % r.pos)
    r.dword(); r.dword(); r.string(); r.dword()
    result = r.data[rs:r.pos]
    home, away, ground, start = struct.unpack('<4H', r.raw(8))
    flag = r.byte()
    if r.word() != 0:
        raise ValueError('fixture with match data at %#x' % r.pos)
    return Fixture(mt[0], mt[1], mt[2], mt[3], mt[4], result, home, away, ground, start, flag)


def parse(data):
    r = Reader(data)
    objects = [None]          # MFC shared class/object index space, 1-based
    created = []
    days = []
    schema = 1
    for d in range(DAYS_PER_FILE):
        if r.word() != d:
            raise ValueError('day %d out of sequence' % d)
        refs = []
        for _ in range(r.dword()):
            tag = r.word()
            if tag == 0xFFFF:
                schema = r.word()
                name = r.raw(r.word()).decode('ascii')
                if name != CLASS_NAME:
                    raise ValueError('unexpected class %s' % name)
                objects.append(CLASS_NAME)
                tag = 0x8000
            if tag & 0x8000:
                f = _read_fixture(r)
                objects.append(f)
                created.append(f)
                refs.append(f)
            else:
                f = objects[tag]
                if not isinstance(f, Fixture):
                    raise ValueError('bad object reference %d' % tag)
                refs.append(f)
        days.append(refs)

    rest = len(data) - r.pos
    # Tail: keys + 18 + 336 + 25 DWORDs + 8 bytes (2000/2002) or + 30 bytes (2006).
    tables = (18 + 336 + 25) * 4
    if rest == 300 * 2 + tables + 8:
        fmt, nkeys = 'classic', 300
    elif rest == 305 * 2 + tables + 30:
        fmt, nkeys = '2006', 305
    elif rest == 300 * 2 + tables + 30:
        fmt, nkeys = '2006', 300      # ICC 2006's 2005 files predate the 305-slot key
    else:
        raise ValueError('unrecognised fixture file tail (%d bytes)' % rest)
    keys = [r.word() for _ in range(nkeys)]
    county_order = [r.dword() for _ in range(18)]
    rotation = [r.dword() for _ in range(336)]
    tournaments = [r.dword() for _ in range(25)]
    tail = r.raw(len(data) - r.pos)

    ff = FixtureFile(_order(created, days), keys, county_order, rotation, tournaments, tail, fmt, schema)
    for d, refs in enumerate(days):
        expect = [f for f in ff.fixtures if f.start <= d < f.start + ff.days_of(f)]
        if [id(f) for f in refs] != [id(f) for f in expect]:
            raise ValueError('day %d does not match its fixtures\' spans' % d)
    return ff


def _order(created, days):
    """One order of all fixtures consistent with the order inside every day."""
    idx = {id(f): i for i, f in enumerate(created)}
    succ = [set() for _ in created]
    indeg = [0] * len(created)
    for refs in days:
        for a, b in zip(refs, refs[1:]):
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
    w = Writer()
    written = {}   # id(fixture) -> object index
    for d in range(DAYS_PER_FILE):
        refs = [f for f in ff.fixtures if f.start <= d < f.start + ff.days_of(f)]
        w.word(d)
        w.dword(len(refs))
        for f in refs:
            if id(f) in written:
                w.word(written[id(f)])
                continue
            w.object_tag(CLASS_NAME, ff.schema)
            written[id(f)] = w._next_index - 1
            w.raw(bytes([f.type & 0xF, f.round, f.out_of, f.day_night & 1, f.tt]))
            w.raw(f.result)
            w.raw(struct.pack('<4HB', f.home, f.away, f.ground, f.start, f.flag & 1))
            w.word(0)   # no match data
    for k in ff.keys:
        w.word(k)
    for v in ff.county_order + ff.rotation + ff.tournaments:
        w.dword(v)
    w.raw(ff.tail)
    return w.getvalue()


def new_fixture(type_, start, home, away):
    return Fixture(type_, 255, 255, 0, 255, BLANK_RESULT, home, away, 0, start, 0)
