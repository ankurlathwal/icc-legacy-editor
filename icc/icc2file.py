"""Parser/serializer for the ICC 2 (International Cricket Captain 2, 1999) database.db.

Unlike the later games, ICC 2 keeps everything in one unencrypted MFC archive
(CrDatabase::Serialize in the ICC 2 CrickMan.dll):

    CrTeamNames       WORD n + n team ids, three CString arrays (name/short/abbrev), WORD
    CrPlayerDatabase  CrDbase of CrBowler + three WORD arrays
    CrCoachDatabase   CrDbase of CrCoach
    CrGroundDatabase  CrDbase of CrGround
    CrTeamDatabase    CrDbase of CrTeam

A CrDbase is a CMapWordToOb (WORD count, then WORD key + object) followed by a
sorted WORD array of the keys and two WORDs (last id, 0).

Abilities are stored unpacked, mostly as 32-bit floats. Values the editor does not
understand are kept as raw bytes so a load/save round trip is byte-exact.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import datetime
import struct

from .archive import Reader, Writer

OLE_EPOCH = datetime.date(1899, 12, 30)
PLAYER_CLASS, COACH_CLASS, GROUND_CLASS, TEAM_CLASS = 'CrBowler', 'CrCoach', 'CrGround', 'CrTeam'

# Career-record blocks are fixed-size arrays in ICC 2 (no presence mask).
BAT_RECORDS, BOWL_RECORDS, FIELD_RECORDS = 16, 16, 15

# One list of record types for the editor; each block stores them in its own order
# (from CrBattingRecord / CrBowlingRecord / CrFieldingRecord::Serialize and getters).
RECORD_TYPES = [
    'First-class career', 'First-class last year', 'First-class this season', 'First-class only',
    'Test career', 'Test this season', 'Test last year',
    'ODI career', 'ODI this season', 'ODI last year',
    'List A career', 'List A this season', 'List A last year',
    'Second XI career', 'Second XI this season', 'Second XI last year',
]
BAT_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8, 10, 12, 11, 13, 14, 15]    # file slot -> RECORD_TYPES index
BOWL_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 11, 13, 14, 15]
FIELD_ORDER = [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]    # no 'First-class only'

# CrBatRecord: 6 WORDs, DWORD, 2 WORDs, 2 DWORDs (28 bytes)
BAT_FMT, BAT_KEYS = '<6HI2H2I', ('debut', 'matches', 'innings', 'not_outs', 'runs', 'highest',
                                  'highest_not_out', 'centuries', 'fifties', 'balls', 'unknown')
# CrBowlRecord: 8 WORDs (16 bytes)
BOWL_FMT, BOWL_KEYS = '<8H', ('balls', 'runs', 'wickets', 'best_runs', 'best_wickets',
                              'five_wickets', 'ten_wickets', 'unknown')
# CrFieldRecord: 2 DWORDs
FIELD_FMT, FIELD_KEYS = '<2I', ('caught', 'stumped')

_BLOCKS = {
    'batting': ('batting_records', 28, BAT_FMT, BAT_KEYS, BAT_ORDER),
    'bowling': ('bowling_records', 16, BOWL_FMT, BOWL_KEYS, BOWL_ORDER),
    'fielding': ('fielding_records', 8, FIELD_FMT, FIELD_KEYS, FIELD_ORDER),
}


def get_record(player, block, rtype):
    """Career record `rtype` (index into RECORD_TYPES) from a block, as a dict (None if absent)."""
    attr, size, fmt, keys, order = _BLOCKS[block]
    if rtype not in order:
        return None
    i = order.index(rtype)
    return dict(zip(keys, struct.unpack(fmt, getattr(player, attr)[size * i:size * (i + 1)])))


def set_record(player, block, rtype, values):
    """Update (or with values=None, clear) a career record. Unknown keys keep their value."""
    attr, size, fmt, keys, order = _BLOCKS[block]
    if rtype not in order:
        return
    i = order.index(rtype)
    blob = bytearray(getattr(player, attr))
    cur = dict(zip(keys, struct.unpack(fmt, blob[size * i:size * (i + 1)])))
    if values is None:
        cur = {k: 0 for k in keys}
    else:
        for k, v in values.items():
            if k in cur and k != 'unknown':
                cur[k] = int(v)
    blob[size * i:size * (i + 1)] = struct.pack(fmt, *(cur[k] for k in keys))
    setattr(player, attr, bytes(blob))


# ------------------------------------------------------------ primitives
def _read_arr(r, size):
    """CArray: WORD count then `size`-byte elements."""
    n = r.word()
    return [int.from_bytes(r.raw(size), 'little') for _ in range(n)]


def _write_arr(w, arr, size):
    w.word(len(arr))
    for v in arr:
        w.raw(int(v).to_bytes(size, 'little'))


def _read_sarr(r):
    return [r.string() for _ in range(r.word())]


def _write_sarr(w, arr):
    w.word(len(arr))
    for s in arr:
        w.string(s)


def f32(b):
    return struct.unpack('<f', b)[0]


def to_f32(v):
    return struct.pack('<f', float(v))


# --------------------------------------------------------------- people
PERSON_ARRAYS = (4, 4, 4, 2, 4)   # element sizes of CrPerson's five CArrays


@dataclass
class Person:
    colour: int
    arrays: List[List[int]]
    ref: int
    first_name: str
    surname: str
    initials: str
    dob_status: int
    dob: float
    team: int
    nation: int
    values: List[bytes]      # 6 x 4 bytes: [?, ?, ?, wage, expected wage, minimum wage] (floats)
    notes: str

    @property
    def name(self):
        return ('%s %s' % (self.first_name, self.surname)).strip()

    @property
    def birthday(self):
        return OLE_EPOCH + datetime.timedelta(days=int(self.dob))

    @birthday.setter
    def birthday(self, d):
        self.dob = float((d - OLE_EPOCH).days)


def _read_person(r):
    colour = r.dword()
    arrays = [_read_arr(r, s) for s in PERSON_ARRAYS]
    ref = r.word()
    first, sur, ini = r.string(), r.string(), r.string()
    st = r.dword()
    (dob,) = struct.unpack('<d', r.raw(8))
    team, nation = r.word(), r.word()
    values = [r.raw(4) for _ in range(6)]
    return Person(colour, arrays, ref, first, sur, ini, st, dob, team, nation, values, r.string())


def _write_person(w, p):
    w.dword(p.colour)
    for arr, s in zip(p.arrays, PERSON_ARRAYS):
        _write_arr(w, arr, s)
    w.word(p.ref)
    w.string(p.first_name); w.string(p.surname); w.string(p.initials)
    w.dword(p.dob_status); w.raw(struct.pack('<d', p.dob))
    w.word(p.team); w.word(p.nation)
    for v in p.values:
        w.raw(v)
    w.string(p.notes)


# ---------------------------------------------------------------- players
@dataclass(frozen=True)
class Field:
    name: str
    label: str
    where: str        # 'ptail' (CrPlayer values), 'btail' (CrBowler values), 'person'
    index: int
    kind: str         # 'f' float, 'i' int, 'b' 0/1 flag
    group: str


# Named from the ICC 2 getters (CrPlayer / CrBowler / CrPerson member offsets).
FIELDS = [
    Field('batting', 'Batting ability', 'ptail', 14, 'f', 'Batting'),
    Field('aggression', 'Aggression', 'ptail', 0, 'f', 'Batting'),
    Field('off_side', 'Off-side play', 'ptail', 10, 'f', 'Batting'),
    Field('front_foot', 'Front-foot play', 'ptail', 8, 'f', 'Batting'),
    Field('vs_fast', 'Against fast bowling', 'ptail', 7, 'f', 'Batting'),
    Field('test_rating', 'Test rating', 'ptail', 15, 'f', 'Batting'),
    Field('oneday_rating', 'List A rating', 'ptail', 16, 'f', 'Batting'),
    Field('odi_rating', 'ODI rating', 'ptail', 17, 'f', 'Batting'),
    Field('ability_adjuster', 'Ability adjuster', 'ptail', 2, 'f', 'Batting'),
    Field('bowling', 'Bowling ability (lower = better)', 'btail', 6, 'f', 'Bowling'),
    Field('accuracy', 'Economy (lower = tighter)', 'btail', 0, 'f', 'Bowling'),
    Field('right_arm', 'Right-arm bowler', 'btail', 7, 'b', 'Bowling'),
    Field('fielding', 'Fielding', 'ptail', 22, 'f', 'Fielding'),
    Field('wicketkeeper', 'Wicketkeeper', 'ptail', 13, 'b', 'Role'),
    Field('part_time_keeper', 'Part-time keeper', 'ptail', 18, 'b', 'Role'),
    Field('right_handed', 'Right-handed batsman', 'ptail', 12, 'b', 'Role'),
    Field('basic_fitness', 'Basic fitness', 'ptail', 1, 'f', 'Fitness'),
    Field('current_fitness', 'Current fitness', 'ptail', 5, 'f', 'Fitness'),
    Field('match_fitness', 'Match fitness', 'ptail', 6, 'f', 'Fitness'),
    Field('batting_coaching', 'Batting coaching', 'ptail', 3, 'f', 'Status'),
    Field('coach_batting', 'Batting coach focus', 'ptail', 4, 'i', 'Status'),
    Field('missed_innings', 'Missed innings', 'ptail', 9, 'i', 'Status'),
    Field('international_duty', 'On international duty', 'ptail', 19, 'b', 'Status'),
    Field('bowling_coaching', 'Bowling coaching', 'btail', 1, 'f', 'Status'),
    Field('coach_bowling', 'Bowling coach focus', 'btail', 2, 'i', 'Status'),
    Field('match_wickets', 'Wickets this match', 'btail', 10, 'i', 'Status'),
    Field('match_runs', 'Runs conceded this match', 'btail', 11, 'i', 'Status'),
]
FIELDS_BY_NAME = {f.name: f for f in FIELDS}

# Same codes as ICC 2000/2002 (checked against well-known players).
BOWLER_TYPES = {0: 'Finger spin (off spin / slow left-arm)', 1: 'Wrist spin (leg spin)', 2: 'Medium',
                3: 'Medium-fast', 4: 'Fast-medium', 5: 'Fast'}
BAT_TYPES = {0: 'Opener', 1: 'Middle order', 2: 'All-rounder', 3: 'Tail-ender'}


@dataclass
class Player:
    key: int
    person: Person
    batting_records: bytes    # 16 x CrBatRecord (28 bytes) + DWORD
    bowling_records: bytes    # 16 x CrBowlRecord (16 bytes) + DWORD
    fielding_records: bytes   # 15 x CrFieldRecord (8 bytes) + DWORD
    injury: bytes             # CrInjType, 4 DWORDs
    injury_prone: int
    bat_type: int
    form: bytes               # CrForm, 12 x (bat 7 + bowl 4) + 2 DWORDs
    ptail: List[bytes]        # 23 x 4 bytes (CrPlayer)
    bowler_type: int
    btail: List[bytes]        # 12 x 4 bytes (CrBowler)

    def __getattr__(self, name):
        # expose the person's fields directly (first_name, team, ...)
        if name != 'person' and 'person' in self.__dict__ and hasattr(self.__dict__['person'], name):
            return getattr(self.__dict__['person'], name)
        raise AttributeError(name)

    def get(self, name):
        f = FIELDS_BY_NAME[name]
        raw = getattr(self, f.where)[f.index]
        if f.kind == 'f':
            return f32(raw)
        return int.from_bytes(raw, 'little')

    def set(self, name, value):
        f = FIELDS_BY_NAME[name]
        if f.kind == 'f':
            raw = to_f32(value)
        else:
            v = int(value)
            if f.kind == 'b' and v not in (0, 1):
                raise ValueError('%s must be 0 or 1' % name)
            raw = (v & 0xFFFFFFFF).to_bytes(4, 'little')
        getattr(self, f.where)[f.index] = raw

    # money (floats in the person block)
    def money(self, i):
        return f32(self.person.values[i])

    def set_money(self, i, v):
        self.person.values[i] = to_f32(v)


def _read_player(r, key):
    person = _read_person(r)
    bat = r.raw(BAT_RECORDS * 28 + 4)
    bowl = r.raw(BOWL_RECORDS * 16 + 4)
    fld = r.raw(FIELD_RECORDS * 8 + 4)
    inj = r.raw(16)
    prone, battype = r.dword(), r.dword()
    form = r.raw(12 * 11 + 8)
    ptail = [r.raw(4) for _ in range(23)]
    btype = r.dword()
    btail = [r.raw(4) for _ in range(12)]
    return Player(key, person, bat, bowl, fld, inj, prone, battype, form, ptail, btype, btail)


def _write_player(w, p):
    _write_person(w, p.person)
    for b in (p.batting_records, p.bowling_records, p.fielding_records, p.injury):
        w.raw(b)
    w.dword(p.injury_prone); w.dword(p.bat_type)
    w.raw(p.form)
    for b in p.ptail:
        w.raw(b)
    w.dword(p.bowler_type)
    for b in p.btail:
        w.raw(b)


# ---------------------------------------------------------- other objects
@dataclass
class Coach:
    key: int
    person: Person
    coach_type: int
    values: bytes      # 2 DWORDs


@dataclass
class Ground:
    key: int
    conditions: bytes  # CrPitchSpin (8) + CrPitchQuality (12) + CrWeatherType (12)
    id: int
    name: str
    full_name: str
    address: str
    town: str
    description: str
    capacity: int


# CrTeamRecords (ICC 2): 2 CArrays, 10 partnership records, then these values.
# Codes: D = DWORD, W = WORD, S = CString.
RECORD_LAYOUT = 'DWSDDWSDDSDDSDSDDWSDSDDWSDDDSWS' + 'D' * 13
RECORD_NAMES = [
    'highest_total', 'highest_total_opp', 'highest_total_venue', 'highest_total_year',
    'lowest_total', 'lowest_total_opp', 'lowest_total_venue', 'lowest_total_year',
    'season_runs', 'season_runs_player', 'season_runs_year',
    'season_wickets', 'season_wickets_player', 'season_wickets_year',
    'best_match_player', 'best_match_wickets', 'best_match_runs', 'best_match_opp', 'best_match_venue', 'best_match_year',
    'best_innings_player', 'best_innings_wickets', 'best_innings_runs', 'best_innings_opp', 'best_innings_venue', 'best_innings_year',
    'highest_score', 'highest_score_not_out', 'highest_score_player', 'highest_score_opp', 'highest_score_venue',
    'highest_score_year',
] + ['unknown_%d' % i for i in range(12)]


@dataclass
class Partnership:
    year: int
    opponent: int
    runs: int
    batter1: str
    batter2: str
    not_out: int
    venue: str


@dataclass
class RecordBook:
    arrays: List[List[int]]         # two 4-byte CArrays
    partnerships: List[Partnership]  # wickets 1..10
    values: list                     # RECORD_LAYOUT values, named by RECORD_NAMES


def _read_records(r):
    arrays = [_read_arr(r, 4), _read_arr(r, 4)]
    parts = [Partnership(r.dword(), r.word(), r.dword(), r.string(), r.string(), r.dword(), r.string())
             for _ in range(10)]
    vals = [r.dword() if c == 'D' else r.word() if c == 'W' else r.string() for c in RECORD_LAYOUT]
    return RecordBook(arrays, parts, vals)


def _write_records(w, rb):
    for a in rb.arrays:
        _write_arr(w, a, 4)
    for p in rb.partnerships:
        w.dword(p.year); w.word(p.opponent); w.dword(p.runs)
        w.string(p.batter1); w.string(p.batter2); w.dword(p.not_out); w.string(p.venue)
    for c, v in zip(RECORD_LAYOUT, rb.values):
        (w.dword if c == 'D' else w.word if c == 'W' else w.string)(v)


# Team.fixed (84 bytes), named from CrTeam's getters: (name, offset, 'W' WORD / 'D' DWORD)
TEAM_FIXED = [
    ('name_ref2', 0, 'W'), ('name_ref', 2, 'W'), ('year_founded', 4, 'D'), ('coach_ref', 8, 'W'),
    ('unknown_10', 10, 'D'), ('physio_ref', 14, 'W'),
    ('captain', 16, 'D'), ('keeper', 20, 'D'), ('opening_bowler1', 24, 'D'), ('opening_bowler2', 28, 'D'),
    ('yearly_income', 32, 'D'), ('extra_income', 36, 'D'), ('unknown_money', 40, 'D'),
    ('youth_budget', 44, 'D'), ('physio_budget', 48, 'D'), ('coaching_budget', 52, 'D'),
    ('national', 56, 'D'), ('retirements', 60, 'D'), ('extra_costs', 64, 'D'),
    ('last_history_year', 68, 'D'), ('unknown_72', 72, 'D'), ('country', 76, 'D'), ('minor', 80, 'D'),
]
TEAM_FIXED_BY_NAME = {n: (o, t) for n, o, t in TEAM_FIXED}


@dataclass
class Team:
    key: int
    lists: List[List[int]]      # 5 WORD arrays: [home grounds, squad, picked XI, ?, ?]
    history: List[List[str]]    # 4 string arrays of past-season results
    colour: int
    records: List[RecordBook]   # [first-class, one-day]
    fixed: bytes                # 84 bytes of team values (name ref, founded, budgets...)
    test_history: bytes         # 576 bytes

    def value(self, name):
        off, t = TEAM_FIXED_BY_NAME[name]
        return struct.unpack_from('<H' if t == 'W' else '<I', self.fixed, off)[0]

    def set_value(self, name, v):
        off, t = TEAM_FIXED_BY_NAME[name]
        b = bytearray(self.fixed)
        struct.pack_into('<H' if t == 'W' else '<I', b, off, int(v) & (0xFFFF if t == 'W' else 0xFFFFFFFF))
        self.fixed = bytes(b)

    @property
    def squad(self):
        return self.lists[1]

    @property
    def selected(self):
        return self.lists[2]


def _read_team(r, key):
    lists = [_read_arr(r, 2) for _ in range(5)]
    history = [_read_sarr(r) for _ in range(4)]
    colour = r.dword()
    records = [_read_records(r), _read_records(r)]
    return Team(key, lists, history, colour, records, r.raw(84), r.raw(576))


def _write_team(w, t):
    for a in t.lists:
        _write_arr(w, a, 2)
    for a in t.history:
        _write_sarr(w, a)
    w.dword(t.colour)
    for rb in t.records:
        _write_records(w, rb)
    w.raw(t.fixed); w.raw(t.test_history)


# ------------------------------------------------------------- database
@dataclass
class Dbase:
    cls: str
    items: list
    schema: int
    index: List[int]
    last_id: int
    zero: int
    extra: List[List[int]] = field(default_factory=list)   # player database only


def _read_dbase(r, cls, read_obj, extra_arrays=0):
    n = r.word()
    items, schema = [], 1
    for _ in range(n):
        key = r.word()
        s = r.object_tag(cls)
        if s is not None:
            schema = s
        items.append(read_obj(r, key))
    index = _read_arr(r, 2)
    last_id, zero = r.word(), r.word()
    extra = [_read_arr(r, 2) for _ in range(extra_arrays)]
    return Dbase(cls, items, schema, index, last_id, zero, extra)


def _write_dbase(w, db, write_obj):
    w.word(len(db.items))
    for it in db.items:
        w.word(it.key)
        w.object_tag(db.cls, db.schema)
        write_obj(w, it)
    _write_arr(w, db.index, 2)
    w.word(db.last_id); w.word(db.zero)
    for a in db.extra:
        _write_arr(w, a, 2)


@dataclass
class TeamName:
    id: int
    name: str
    short_name: str
    abbrev: str


@dataclass
class ICC2Database:
    team_names: List[TeamName]
    team_names_word: int
    players: Dbase
    coaches: Dbase
    grounds: Dbase
    teams: Dbase

    game_version = 1999

    def player_by_key(self, key):
        return next((p for p in self.players.items if p.key == key), None)


def parse(data):
    r = Reader(data)
    ids = [r.word() for _ in range(r.word())]
    names, shorts, abbrevs = _read_sarr(r), _read_sarr(r), _read_sarr(r)
    names_word = r.word()
    tn = [TeamName(*t) for t in zip(ids, names, shorts, abbrevs)]
    players = _read_dbase(r, PLAYER_CLASS, _read_player, extra_arrays=3)
    coaches = _read_dbase(r, COACH_CLASS, lambda r, k: Coach(k, _read_person(r), r.dword(), r.raw(8)))
    grounds = _read_dbase(r, GROUND_CLASS, lambda r, k: Ground(
        k, r.raw(32), r.word(), r.string(), r.string(), r.string(), r.string(), r.string(), r.dword()))
    teams = _read_dbase(r, TEAM_CLASS, _read_team)
    if not r.eof():
        raise ValueError('unexpected data after the team database at %#x' % r.pos)
    return ICC2Database(tn, names_word, players, coaches, grounds, teams)


def serialize(db):
    w = Writer()
    w.word(len(db.team_names))
    for t in db.team_names:
        w.word(t.id)
    for attr in ('name', 'short_name', 'abbrev'):
        w.word(len(db.team_names))
        for t in db.team_names:
            w.string(getattr(t, attr))
    w.word(db.team_names_word)
    _write_dbase(w, db.players, _write_player)
    _write_dbase(w, db.coaches, lambda w, c: (_write_person(w, c.person), w.dword(c.coach_type), w.raw(c.values)))

    def write_ground(w, g):
        w.raw(g.conditions); w.word(g.id)
        for s in (g.name, g.full_name, g.address, g.town, g.description):
            w.string(s)
        w.dword(g.capacity)
    _write_dbase(w, db.grounds, write_ground)
    _write_dbase(w, db.teams, _write_team)
    return w.getvalue()


def load(path):
    with open(path, 'rb') as f:
        return parse(f.read())


def save(db, path):
    with open(path, 'wb') as f:
        f.write(serialize(db))
