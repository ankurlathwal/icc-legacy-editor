"""Parser/serializer for the ICC 1 engine's database.db: International Cricket Captain (1998)
and Australian Cricket Captain (1998).

Both games are one generation before ICC 2. The file has the same outline as ICC 2's (see
icc2file): one unencrypted MFC archive holding CrTeamNames, then CrDbase maps of CrBowler
(players), CrCoach, CrGround and CrTeam. The classes are smaller and keep their abilities as
64-bit doubles (layouts from each game's CrickMan.dll / CrTypes.dll Serialize functions, names
from their getters):

    CrPerson  as ICC 2 up to the nationality, then morale and loyalty (doubles), contract
              length (DWORD) and wage / expected wage / minimum wage (floats); no notes.
    CrPlayer  batting + bowling records (8 DWORDs each) and fielding records (2 DWORDs), each
              block followed by a DWORD; the injury type (4 DWORDs), injury proneness, bat type,
              then PTAIL (abilities, see FIELDS).
    CrBowler  bowler type, then BTAIL.
    CrCoach   person, coach type, rating (double), remaining sessions.
    CrGround  24 bytes of pitch / weather settings, id, five strings, capacity.
    CrTeam    ground / squad / XI lists, CrCountry (28 bytes), two more player lists, four
              history lists, colour and team values (TEAM_FIXED), a history block (216 / 576 bytes).
              No club record books.

The two games differ only in the record types (ACC adds 'First-class only' and the Second XI)
and the team record (ICC 1998 keeps its Championship / Sunday League history as numbers, the
colour among the team values, has no default team size and a smaller history block); see
Format, ICC1 and ACC.

Values the editor does not understand are kept raw so a load/save round trip is byte-exact.
"""
from dataclasses import dataclass, field
from typing import List
import datetime
import struct

from .archive import Reader, Writer
from .icc2file import (OLE_EPOCH, Coach, Dbase, Ground, TeamName, PLAYER_CLASS, COACH_CLASS, GROUND_CLASS,
                       TEAM_CLASS, BOWLER_TYPES, BAT_TYPES, PERSON_ARRAYS, _read_arr, _write_arr, _read_sarr,
                       _write_sarr, _read_dbase, _write_dbase)

# CrBatRecord / CrBowlRecord: 8 DWORDs; CrFieldRecord: 2 DWORDs. Best bowling is runs then wickets
# (Warne's 8/71), although the DLL's setBBWickets/setBBRuns names say the opposite.
BAT_FMT, BAT_KEYS = '<8I', ('debut', 'matches', 'innings', 'not_outs', 'runs', 'highest', 'centuries', 'fifties')
BOWL_FMT, BOWL_KEYS = '<8I', ('balls', 'runs', 'wickets', 'best_runs', 'best_wickets', 'five_wickets',
                              'ten_wickets', 'unknown')
FIELD_FMT, FIELD_KEYS = '<2I', ('caught', 'stumped')
BAT_COLUMNS = ['matches', 'innings', 'not_outs', 'runs', 'highest', 'centuries', 'fifties', 'caught', 'stumped', 'debut']
BOWL_COLUMNS = ['balls', 'runs', 'wickets', 'best_wickets', 'best_runs', 'five_wickets', 'ten_wickets']


# --------------------------------------------------------------- people
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
    morale: float
    loyalty: float
    contract_length: int
    wages: List[float]       # [wage, expected wage, minimum wage]
    notes = None             # these games have no notes

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
    morale, loyalty = struct.unpack('<2d', r.raw(16))
    contract = r.dword()
    wages = list(struct.unpack('<3f', r.raw(12)))
    return Person(colour, arrays, ref, first, sur, ini, st, dob, team, nation, morale, loyalty, contract, wages)


def _write_person(w, p):
    w.dword(p.colour)
    for arr, s in zip(p.arrays, PERSON_ARRAYS):
        _write_arr(w, arr, s)
    w.word(p.ref)
    w.string(p.first_name); w.string(p.surname); w.string(p.initials)
    w.dword(p.dob_status); w.raw(struct.pack('<d', p.dob))
    w.word(p.team); w.word(p.nation)
    w.raw(struct.pack('<2d', p.morale, p.loyalty))
    w.dword(p.contract_length)
    w.raw(struct.pack('<3f', *p.wages))


# ---------------------------------------------------------------- players
@dataclass(frozen=True)
class Field:
    name: str
    label: str
    where: str        # 'ptail' (CrPlayer values), 'btail' (CrBowler values)
    index: int        # byte offset inside that block
    kind: str         # 'f' double, 'i' int, 'b' 0/1 flag
    group: str


# Byte offsets into PTAIL / BTAIL (same in both games), named from the CrPlayer / CrBowler getters.
FIELDS = [
    Field('batting', 'Batting ability', 'ptail', 144, 'f', 'Batting'),
    Field('aggression', 'Aggression', 'ptail', 128, 'f', 'Batting'),
    Field('off_side', 'Off-side play', 'ptail', 192, 'f', 'Batting'),
    Field('front_foot', 'Front-foot play', 'ptail', 180, 'f', 'Batting'),
    Field('vs_fast', 'Against fast bowling', 'ptail', 172, 'f', 'Batting'),
    Field('batting_at_27', 'Batting at 27 (potential)', 'ptail', 212, 'f', 'Batting'),
    Field('test_rating', 'Test rating', 'ptail', 220, 'f', 'Batting'),
    Field('bowling', 'Bowling ability (lower = better)', 'btail', 164, 'f', 'Bowling'),
    Field('accuracy', 'Economy (lower = tighter)', 'btail', 128, 'f', 'Bowling'),
    Field('right_arm', 'Right-arm bowler', 'btail', 172, 'b', 'Bowling'),
    Field('catching', 'Catching', 'ptail', 244, 'f', 'Fielding'),
    Field('stopping', 'Ground fielding', 'ptail', 260, 'f', 'Fielding'),
    Field('throwing', 'Throwing', 'ptail', 268, 'f', 'Fielding'),
    Field('running_speed', 'Running speed', 'ptail', 252, 'f', 'Fielding'),
    Field('wicketkeeper', 'Wicketkeeper', 'ptail', 208, 'b', 'Role'),
    Field('part_time_keeper', 'Part-time keeper', 'ptail', 228, 'b', 'Role'),
    Field('right_handed', 'Right-handed batsman', 'ptail', 204, 'b', 'Role'),
    Field('basic_fitness', 'Basic fitness', 'ptail', 136, 'f', 'Fitness'),
    Field('current_fitness', 'Current fitness', 'ptail', 164, 'f', 'Fitness'),
    Field('batting_coaching', 'Batting coaching', 'ptail', 152, 'f', 'Status'),
    Field('coach_batting', 'Batting coach focus', 'ptail', 160, 'i', 'Status'),
    Field('batting_form', 'Batting form counter', 'ptail', 188, 'i', 'Status'),
    Field('batting_match_ready', 'Batting match readiness', 'ptail', 236, 'f', 'Status'),
    Field('international_duty', 'On international duty', 'ptail', 232, 'b', 'Status'),
    Field('bowling_coaching', 'Bowling coaching', 'btail', 136, 'f', 'Status'),
    Field('coach_bowling', 'Bowling coach focus', 'btail', 144, 'i', 'Status'),
    Field('coach_fielding', 'Fielding coach focus', 'btail', 148, 'i', 'Status'),
    Field('bowling_match_ready', 'Bowling match readiness', 'btail', 176, 'f', 'Status'),
    Field('match_wickets', 'Wickets this match', 'btail', 192, 'i', 'Status'),
]
FIELDS_BY_NAME = {f.name: f for f in FIELDS}
PTAIL_SIZE, BTAIL_SIZE = 276, 196
WAGES = ('wage', 'expected_wage', 'minimum_wage')


@dataclass
class Player:
    key: int
    person: Person
    batting_records: bytes    # n x CrBatRecord (32 bytes) + DWORD
    bowling_records: bytes    # n x CrBowlRecord (32 bytes) + DWORD
    fielding_records: bytes   # n x CrFieldRecord (8 bytes) + DWORD
    injury: bytes             # CrInjType, 4 DWORDs
    injury_prone: int
    bat_type: int
    ptail: bytes              # CrPlayer values (PTAIL_SIZE bytes)
    bowler_type: int
    btail: bytes              # CrBowler values (BTAIL_SIZE bytes)

    def __getattr__(self, name):
        if name != 'person' and 'person' in self.__dict__ and hasattr(self.__dict__['person'], name):
            return getattr(self.__dict__['person'], name)
        raise AttributeError(name)

    def get(self, name):
        f = FIELDS_BY_NAME[name]
        blob = getattr(self, f.where)
        if f.kind == 'f':
            return struct.unpack_from('<d', blob, f.index)[0]
        return struct.unpack_from('<I', blob, f.index)[0]

    def set(self, name, value):
        f = FIELDS_BY_NAME[name]
        blob = bytearray(getattr(self, f.where))
        if f.kind == 'f':
            struct.pack_into('<d', blob, f.index, float(value))
        else:
            v = int(value)
            if f.kind == 'b' and v not in (0, 1):
                raise ValueError('%s must be 0 or 1' % name)
            struct.pack_into('<I', blob, f.index, v & 0xFFFFFFFF)
        setattr(self, f.where, bytes(blob))

    def wage(self, kind):
        return self.person.wages[WAGES.index(kind)]

    def set_wage(self, kind, v):
        self.person.wages[WAGES.index(kind)] = float(v)


# ------------------------------------------------------------------ teams
# Team values, from CrTeam::Serialize and its getters: (name, offset, 'W' WORD / 'D' DWORD)
TEAM_FIXED = [
    ('name_ref', 0, 'W'), ('name_ref2', 2, 'W'), ('year_founded', 4, 'D'), ('coach_ref', 8, 'W'), ('physio_ref', 10, 'W'),
    ('captain', 12, 'D'), ('keeper', 16, 'D'), ('opening_bowler1', 20, 'D'), ('opening_bowler2', 24, 'D'),
    ('yearly_income', 28, 'D'), ('extra_income', 32, 'D'), ('unknown_38c', 36, 'D'), ('youth_budget', 40, 'D'),
    ('national', 44, 'D'), ('retirements', 48, 'D'), ('extra_costs', 52, 'D'), ('last_history_year', 56, 'D'),
    ('default_team_size', 60, 'D'),    # ACC only
]
TEAM_MONEY = ('yearly_income', 'extra_income', 'youth_budget', 'extra_costs')


@dataclass
class Team:
    key: int
    lists: List[List[int]]      # 5 WORD arrays: [home grounds, squad, picked XI, player list, player list]
    country: bytes              # CrCountry, 28 bytes
    history: list               # 4 lists of past-season results (Championship, Sunday League, B&H, NatWest)
    colour: int
    fixed: bytes                # team values, see TEAM_FIXED (Format.fixed_size bytes, colour not included)
    test_history: bytes         # Format.history_size bytes (ICC 1998: 18 x 3 DWORDs, ACC: 576)
    fmt: 'Format' = field(repr=False, compare=False, default=None)

    records = ()                # no club record books

    def has_value(self, name):
        return name in self.fmt.team_fixed

    def value(self, name):
        off, t = self.fmt.team_fixed[name]
        return struct.unpack_from('<H' if t == 'W' else '<I', self.fixed, off)[0]

    def set_value(self, name, v):
        off, t = self.fmt.team_fixed[name]
        b = bytearray(self.fixed)
        struct.pack_into('<H' if t == 'W' else '<I', b, off, int(v) & (0xFFFF if t == 'W' else 0xFFFFFFFF))
        self.fixed = bytes(b)

    @property
    def squad(self):
        return self.lists[1]

    @property
    def selected(self):
        return self.lists[2]


# ------------------------------------------------------------- database
@dataclass
class ACCDatabase:
    team_names: List[TeamName]
    team_names_word: int
    players: Dbase
    coaches: Dbase
    grounds: Dbase
    teams: Dbase
    fmt: 'Format' = field(repr=False, compare=False, default=None)

    @property
    def game_version(self):
        return self.fmt.name

    def player_by_key(self, key):
        return next((p for p in self.players.items if p.key == key), None)


def _read_ground(r, k):
    return Ground(k, r.raw(24), r.word(), r.string(), r.string(), r.string(), r.string(), r.string(), r.dword())


def _write_ground(w, g):
    w.raw(g.conditions); w.word(g.id)
    for s in (g.name, g.full_name, g.address, g.town, g.description):
        w.string(s)
    w.dword(g.capacity)


class Format:
    """One ICC 1 engine game. Also the 'file-format module' the editor store uses (icc2store.F)."""
    FIELDS, FIELDS_BY_NAME = FIELDS, FIELDS_BY_NAME
    BOWLER_TYPES, BAT_TYPES = BOWLER_TYPES, BAT_TYPES
    TEAM_MONEY, BAT_COLUMNS, BOWL_COLUMNS = TEAM_MONEY, BAT_COLUMNS, BOWL_COLUMNS

    def __init__(self, name, title, record_types, field_order, history, colour_in_fixed, fixed_size, history_size):
        self.name, self.title = name, title
        self.RECORD_TYPES = record_types
        self.bat_order = list(range(len(record_types)))     # batting and bowling: every type, in order
        self.field_order = field_order                      # file slot -> RECORD_TYPES index
        self.history = history                              # 'D' DWORD array / 'S' string array, x4
        self.colour_in_fixed = colour_in_fixed              # colour written after the first 8 value bytes
        self.fixed_size = fixed_size
        self.history_size = history_size                    # the block after the team values
        self.team_fixed = {n: (o, t) for n, o, t in TEAM_FIXED if o < fixed_size}
        self.blocks = {
            'batting': ('batting_records', 32, BAT_FMT, BAT_KEYS, self.bat_order),
            'bowling': ('bowling_records', 32, BOWL_FMT, BOWL_KEYS, self.bat_order),
            'fielding': ('fielding_records', 8, FIELD_FMT, FIELD_KEYS, field_order),
        }

    def __repr__(self):
        return 'Format(%s)' % self.name

    # ---- career records
    def get_record(self, player, block, rtype):
        """Career record `rtype` (index into RECORD_TYPES) from a block, as a dict (None if absent)."""
        attr, size, fmt, keys, order = self.blocks[block]
        if rtype not in order:
            return None
        i = order.index(rtype)
        return dict(zip(keys, struct.unpack(fmt, getattr(player, attr)[size * i:size * (i + 1)])))

    def set_record(self, player, block, rtype, values):
        """Update (or with values=None, clear) a career record. Unknown keys keep their value."""
        attr, size, fmt, keys, order = self.blocks[block]
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

    # ---- objects
    def _read_player(self, r, key):
        person = _read_person(r)
        n = len(self.bat_order)
        bat, bowl = r.raw(n * 32 + 4), r.raw(n * 32 + 4)
        fld = r.raw(len(self.field_order) * 8 + 4)
        inj = r.raw(16)
        prone, battype = r.dword(), r.dword()
        ptail = r.raw(PTAIL_SIZE)
        btype = r.dword()
        return Player(key, person, bat, bowl, fld, inj, prone, battype, ptail, btype, r.raw(BTAIL_SIZE))

    @staticmethod
    def _write_player(w, p):
        _write_person(w, p.person)
        for b in (p.batting_records, p.bowling_records, p.fielding_records, p.injury):
            w.raw(b)
        w.dword(p.injury_prone); w.dword(p.bat_type)
        w.raw(p.ptail)
        w.dword(p.bowler_type)
        w.raw(p.btail)

    def _read_team(self, r, key):
        grounds, squad, selected = (_read_arr(r, 2) for _ in range(3))
        country = r.raw(28)
        extra = [_read_arr(r, 2) for _ in range(2)]
        history = [_read_arr(r, 4) if k == 'D' else _read_sarr(r) for k in self.history]
        if self.colour_in_fixed:
            head = r.raw(8)
            colour = r.dword()
            fixed = head + r.raw(self.fixed_size - 8)
        else:
            colour = r.dword()
            fixed = r.raw(self.fixed_size)
        return Team(key, [grounds, squad, selected] + extra, country, history, colour, fixed,
                    r.raw(self.history_size), self)

    def _write_team(self, w, t):
        for a in t.lists[:3]:
            _write_arr(w, a, 2)
        w.raw(t.country)
        for a in t.lists[3:]:
            _write_arr(w, a, 2)
        for k, a in zip(self.history, t.history):
            _write_arr(w, a, 4) if k == 'D' else _write_sarr(w, a)
        if self.colour_in_fixed:
            w.raw(t.fixed[:8]); w.dword(t.colour); w.raw(t.fixed[8:])
        else:
            w.dword(t.colour); w.raw(t.fixed)
        w.raw(t.test_history)

    # ---- whole file
    def parse(self, data):
        r = Reader(data)
        ids = [r.word() for _ in range(r.word())]
        names, shorts, abbrevs = _read_sarr(r), _read_sarr(r), _read_sarr(r)
        names_word = r.word()
        tn = [TeamName(*t) for t in zip(ids, names, shorts, abbrevs)]
        players = _read_dbase(r, PLAYER_CLASS, self._read_player, extra_arrays=3)
        coaches = _read_dbase(r, COACH_CLASS, lambda r, k: Coach(k, _read_person(r), r.dword(), r.raw(12)))
        grounds = _read_dbase(r, GROUND_CLASS, _read_ground)
        teams = _read_dbase(r, TEAM_CLASS, self._read_team)
        if not r.eof():
            raise ValueError('unexpected data after the team database at %#x' % r.pos)
        return ACCDatabase(tn, names_word, players, coaches, grounds, teams, self)

    def serialize(self, db):
        w = Writer()
        w.word(len(db.team_names))
        for t in db.team_names:
            w.word(t.id)
        for attr in ('name', 'short_name', 'abbrev'):
            w.word(len(db.team_names))
            for t in db.team_names:
                w.string(getattr(t, attr))
        w.word(db.team_names_word)
        _write_dbase(w, db.players, self._write_player)
        _write_dbase(w, db.coaches, lambda w, c: (_write_person(w, c.person), w.dword(c.coach_type), w.raw(c.values)))
        _write_dbase(w, db.grounds, _write_ground)
        _write_dbase(w, db.teams, self._write_team)
        return w.getvalue()

    def load(self, path):
        with open(path, 'rb') as f:
            return self.parse(f.read())


# International Cricket Captain (1998): records from its CrBattingRecord / CrFieldingRecord getters.
ICC1 = Format(
    '1998', 'International Cricket Captain Editor',
    ['First-class career', 'First-class last year', 'First-class this season',
     'Test career', 'Test this season', 'Test last year', 'ODI career', 'ODI this season',
     'List A career', 'List A this season'],
    [0, 1, 2, 3, 4, 6, 7, 8, 9],                         # no 'Test last year'
    'DDSS', True, 60, 216)

# Australian Cricket Captain (1998): adds 'First-class only' and the Second XI.
ACC = Format(
    'ACC', 'Australian Cricket Captain Editor',
    ['First-class career', 'First-class last year', 'First-class this season', 'First-class only',
     'Test career', 'Test this season', 'Test last year', 'ODI career', 'ODI this season',
     'List A career', 'List A this season', 'Second XI career', 'Second XI this season'],
    [0, 1, 2, 4, 5, 7, 8, 9, 10, 11, 12],                # no 'First-class only' / 'Test last year'
    'SSSS', False, 64, 576)

FORMATS = (ACC, ICC1)


def parse(data):
    """Parse either game's database.db (the format is detected)."""
    errors = []
    for fmt in FORMATS:
        try:
            return fmt.parse(data)
        except (ValueError, EOFError) as e:
            errors.append('%s: %s' % (fmt.name, e))
    raise ValueError('not an ICC 1998 / Australian Cricket Captain database (%s)' % '; '.join(errors))


def serialize(db):
    return db.fmt.serialize(db)


def load(path):
    with open(path, 'rb') as f:
        return parse(f.read())


def save(db, path):
    with open(path, 'wb') as f:
        f.write(serialize(db))
