"""Parser/serializer for the ICC 2000 / 2002 / 2006 player file (dataT.db).

Layout derived from CrManAndEng.dll / CrTypes.dll (CrDatabase::SerializeTemporary):

    DWORD                       unknown (0)
    CMapWordToOb                player database: WORD count, then count x (WORD key, CrBowler object)
    ...                         team database, coach database and 5000 x 0xFF padding
                                (kept verbatim as `tail`)

Each CrBowler is CrPerson + CrPlayer + CrBowler data. Packed ability words are kept
raw so a load/save round trip is byte-exact; the BITFIELDS table describes how to
read and write individual attributes.

Two on-disk formats exist (see Format): ICC 2000/2002 ("classic") and ICC 2006, which
adds a nationality DWORD, England-contract flag, batting/bowling abilities stored as
doubles, 24 career-record types (Twenty20) and a larger Test-history block, and seeds
the encryption differently. load() detects the format.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import datetime
import struct

from .archive import Reader, Writer
from .crypto import read_db, write_db

PLAYER_CLASS = 'CrBowler'

RECORD_TYPES = [
    'First-class career', 'Test career', 'ODI career', 'List A career',
    'First-class this season', 'Test this season', 'ODI this season', 'List A this season',
    'Second XI career', 'Second XI this season', 'Second XI last year',
    'First-class last year', 'Test last year', 'ODI last year', 'List A last year',
    'First-class only', 'Net first-class', 'Net one-day',
]

RECORD_TYPES_2006 = RECORD_TYPES[:16] + [
    'T20 career', 'T20 this season', 'T20 international career', 'T20 international this season',
    'T20 last year', 'T20 international last year', 'Net first-class', 'Net one-day',
]


@dataclass(frozen=True)
class Format:
    name: str
    seed_with_length: bool        # CrEncryptedSerialize seeds rand() with sum+len (2006)
    record_types: tuple
    mask_words: tuple             # record-type bit carried by each bit of the mask WORDs
    nationality_dword: bool       # CrPerson stores the national team id as a DWORD
    float_abilities: bool         # batting / bowling stored as doubles (x4096 = CrFixed)
    test_history_size: int        # CrTestHistory bytes per national team


CLASSIC = Format('classic', False, tuple(RECORD_TYPES),
                 ((17, 16, 15, 14, 13, 12, 11, 10, 9, 8),), False, False, 144)
FORMAT_2006 = Format('2006', True, tuple(RECORD_TYPES_2006),
                     ((23, 22, 21, 14, 13, 12, 11, 10, 9, 8), (20, 19, 18, 17, 16, 15)), True, True, 160)
FORMATS = (CLASSIC, FORMAT_2006)

# CrPerson national-team nibble -> team id in dataP.db, from each release's
# CrPerson::getNationalTeamRef. Codes 2-13 are the same in both releases; ICC 2002
# added Namibia (1), Canada (14) and the Netherlands (15). (ICC 2000 maps 14 to the
# "None" placeholder team 169, so it is left out here.)
NATIONAL_TABLES = {
    2000: {0: None, 2: 43, 3: 44, 4: 45, 5: 46, 6: 47, 7: 48, 8: 49, 9: 50, 10: 51,
           11: 21, 12: 70, 13: 76},
    2002: {0: None, 1: 75, 2: 43, 3: 44, 4: 45, 5: 46, 6: 47, 7: 48, 8: 49, 9: 50,
           10: 51, 11: 21, 12: 70, 13: 76, 14: 78, 15: 77},
}
NATIONAL_TEAMS = NATIONAL_TABLES[2002]

OLE_EPOCH = datetime.date(1899, 12, 30)

# Value labels, inferred from well-known players in the original database.
BOWLER_TYPES = {0: 'Finger spin (off spin / slow left-arm)', 1: 'Wrist spin (leg spin)', 2: 'Medium',
                3: 'Medium-fast', 4: 'Fast-medium', 5: 'Fast', 6: 'Type 6', 7: 'Type 7'}
BAT_TYPES = {0: 'Opener', 1: 'Middle order', 2: 'All-rounder', 3: 'Tail-ender',
             4: 'Type 4', 5: 'Type 5', 6: 'Type 6', 7: 'Type 7'}


# ------------------------------------------------------------ bitfields
@dataclass(frozen=True)
class BitField:
    name: str
    label: str
    word: str          # attribute on Player holding the raw value
    shift: int
    bits: int
    lo: Optional[float] = None   # scaled range (game's uintToAbility), if any
    hi: Optional[float] = None
    group: str = ''

    @property
    def max_raw(self):
        return (1 << self.bits) - 1

    def get(self, player):
        return (getattr(player, self.word) >> self.shift) & self.max_raw

    def set(self, player, value):
        value = int(value)
        if not 0 <= value <= self.max_raw:
            raise ValueError('%s must be 0..%d' % (self.name, self.max_raw))
        mask = self.max_raw << self.shift
        setattr(player, self.word, (getattr(player, self.word) & ~mask) | (value << self.shift))

    def scaled(self, raw):
        if self.lo is None:
            return raw
        return self.lo + raw * (self.hi - self.lo) / self.max_raw


BITFIELDS = [
    # CrPerson packed bytes (person_bits = b0 | b1<<8 | b2<<16)
    BitField('national_team', 'National team', 'person_bits', 4, 4, group='Personal'),
    BitField('skin_tone', 'Skin tone (graphics)', 'person_bits', 8, 1, group='Personal'),
    BitField('loyalty', 'Loyalty', 'person_bits', 9, 3, group='Personal'),
    BitField('contract', 'Contract length (+1)', 'person_bits', 12, 3, group='Personal'),

    # CrPlayer DWORD A
    BitField('missed_innings', 'Missed innings', 'player_a', 0, 4, group='Status'),
    BitField('coach_batting', 'Batting coach focus', 'player_a', 4, 2, group='Status'),
    BitField('batting_coaching', 'Batting coaching', 'player_a', 6, 5, group='Status'),
    BitField('odi_rating', 'ODI rating', 'player_a', 11, 3, group='Batting'),
    BitField('oneday_rating', 'List A rating', 'player_a', 14, 3, group='Batting'),
    BitField('test_rating', 'Test rating', 'player_a', 17, 3, group='Batting'),
    BitField('off_side', 'Off-side play', 'player_a', 20, 3, group='Batting'),
    BitField('front_foot', 'Front-foot play', 'player_a', 23, 3, group='Batting'),
    BitField('vs_fast', 'Against fast bowling', 'player_a', 26, 3, group='Batting'),
    BitField('aggression', 'Aggression', 'player_a', 29, 3, group='Batting'),
    # CrPlayer DWORD B
    BitField('wicketkeeper', 'Wicketkeeper', 'player_b', 0, 1, group='Role'),
    BitField('batting_form', 'Batting form', 'player_b', 1, 5, 0, 100, group='Batting'),
    BitField('current_fitness', 'Current fitness', 'player_b', 6, 4, 60, 100, group='Fitness'),
    BitField('batting', 'Batting ability', 'player_b', 10, 9, 5, 130, group='Batting'),
    BitField('bat_type', 'Batting role', 'player_b', 19, 3, group='Role'),
    BitField('currently_playing', 'Currently playing', 'player_b', 22, 1, group='Status'),
    BitField('international_duty', 'On international duty', 'player_b', 23, 1, group='Status'),
    BitField('right_handed', 'Right-handed batsman', 'player_b', 24, 1, group='Role'),
    BitField('basic_fitness', 'Basic fitness', 'player_b', 25, 3, group='Fitness'),
    BitField('days_since_match', 'Days since last match', 'player_b', 28, 4, group='Status'),
    # CrPlayer BYTE C
    BitField('fielding', 'Fielding', 'player_c', 0, 4, 0, 100, group='Fielding'),
    BitField('part_time_keeper', 'Part-time keeper', 'player_c', 4, 1, group='Role'),

    # CrBowler DWORD A
    BitField('physio', 'Seeing physio', 'bowler_a', 0, 1, group='Fitness'),
    BitField('bowling_form', 'Bowling form', 'bowler_a', 1, 5, 0, 100, group='Bowling'),
    BitField('bowling', 'Bowling ability (lower = better)', 'bowler_a', 6, 9, 27, 130, group='Bowling'),
    BitField('right_arm', 'Right-arm bowler', 'bowler_a', 15, 1, group='Bowling'),
    BitField('bowler_type', 'Bowler type', 'bowler_a', 16, 3, group='Bowling'),
    BitField('coach_bowling', 'Bowling coach focus', 'bowler_a', 19, 2, group='Status'),
    BitField('bowling_coaching', 'Bowling coaching', 'bowler_a', 21, 6, group='Status'),
    BitField('accuracy', 'Economy (lower = tighter)', 'bowler_a', 27, 5, 0.86, 1.45, group='Bowling'),
    # CrBowler BYTE B / WORD C
    BitField('match_wickets', 'Wickets this match', 'bowler_b', 0, 4, group='Status'),
    BitField('match_runs', 'Runs conceded this match', 'bowler_c', 0, 10, group='Status'),
]
BITFIELDS_BY_NAME = {f.name: f for f in BITFIELDS}


# ------------------------------------------------------------- records
@dataclass
class BatRecord:
    runs: int
    not_outs: int
    innings: int
    centuries: int
    matches: int
    debut: int
    balls: int
    unknown: int
    highest: int
    highest_not_out: int
    fifties: int
    catch_stump_lo: int     # stumped << 4 | caught & 0xF
    catch_stump_hi: int     # caught >> 4

    FORMAT = '<HBHBHHIIHHHII'

    @property
    def caught(self):
        return (self.catch_stump_hi << 4) | (self.catch_stump_lo & 0xF)

    @caught.setter
    def caught(self, v):
        self.catch_stump_lo = (self.catch_stump_lo & ~0xF) | (v & 0xF)
        self.catch_stump_hi = v >> 4

    @property
    def stumped(self):
        return self.catch_stump_lo >> 4

    @stumped.setter
    def stumped(self, v):
        self.catch_stump_lo = (v << 4) | (self.catch_stump_lo & 0xF)


@dataclass
class BowlRecord:
    packed: int      # balls << 12 | wickets << 1 | runs & 1
    runs_hi: int     # runs >> 1
    unknown: int
    best_wickets: int
    best_runs: int
    five_wickets: int
    ten_wickets: int

    FORMAT = '<IHHBBBB'

    @property
    def balls(self):
        return self.packed >> 12

    @balls.setter
    def balls(self, v):
        self.packed = (v << 12) | (self.packed & 0xFFF)

    @property
    def wickets(self):
        return (self.packed >> 1) & 0x7FF

    @wickets.setter
    def wickets(self, v):
        self.packed = (self.packed & ~0xFFE) | ((v & 0x7FF) << 1)

    @property
    def runs(self):
        return (self.runs_hi << 1) | (self.packed & 1)

    @runs.setter
    def runs(self, v):
        self.runs_hi = v >> 1
        self.packed = (self.packed & ~1) | (v & 1)


def _read_mask(r, fmt=CLASSIC):
    """Which career-record types are stored: a BYTE (types 7..0) then one WORD per
    entry of fmt.mask_words, each listing the record type carried by bits 0, 1, 2..."""
    b = r.byte()
    m = 0
    for k in range(8):
        if b >> (7 - k) & 1:
            m |= 1 << k
    for bits in fmt.mask_words:
        v = r.word()
        if v >> len(bits):
            raise ValueError('unexpected record-mask bits %#x' % v)
        for i, rec in enumerate(bits):
            if v >> i & 1:
                m |= 1 << rec
    return m


def _write_mask(w, m, fmt=CLASSIC):
    w.byte(sum(1 << (7 - k) for k in range(8) if m >> k & 1))
    for bits in fmt.mask_words:
        w.word(sum(1 << i for i, rec in enumerate(bits) if m >> rec & 1))


# -------------------------------------------------------------- player
@dataclass
class Player:
    key: int                  # map key the game files the player under
    team: int                 # first-class team id (dataP.db)
    ref: int                  # original player id
    dob_status: int
    dob: float                # OLE date (days since 1899-12-30)
    notes: str
    first_name: str
    surname: str
    initials: str
    wage: int                 # units of 500
    expected_wage: int
    minimum_wage: int
    person_bits: int          # 3 packed bytes
    batting_records: Dict[int, BatRecord]
    bowling_records: Dict[int, BowlRecord]
    injury: int               # type << 1 | injured
    injury_extra: Optional[int]
    form: bytes               # CrForm, 134 bytes (recent match form, kept raw)
    international_rating: bytes  # 18 bytes, kept raw
    player_a: int
    player_b: int
    player_c: int
    bowler_a: int
    bowler_b: int
    bowler_c: int
    # ICC 2006 only
    national_id: Optional[int] = None       # national team id (replaces the 4-bit code)
    england_contracted: Optional[int] = None
    batting_value: Optional[float] = None   # batting ability, same scale as BITFIELDS 'batting'
    bowling_value: Optional[float] = None   # bowling ability (lower = better)

    def __getitem__(self, name):
        return BITFIELDS_BY_NAME[name].get(self)

    def __setitem__(self, name, value):
        BITFIELDS_BY_NAME[name].set(self, value)

    @property
    def name(self):
        return ('%s %s' % (self.first_name, self.surname)).strip()

    @property
    def birthday(self):
        return OLE_EPOCH + datetime.timedelta(days=int(self.dob))

    @birthday.setter
    def birthday(self, d):
        self.dob = float((d - OLE_EPOCH).days)

    @property
    def injured(self):
        return bool(self.injury & 1)



def _read_player(r, key, fmt=CLASSIC):
    team, ref, dob_status = r.byte(), r.word(), r.dword()
    (dob,) = struct.unpack('<d', r.raw(8))
    notes, first, sur, ini = r.string(), r.string(), r.string(), r.string()
    wage, exp, mn = r.dword(), r.dword(), r.dword()
    national_id = r.dword() if fmt.nationality_dword else None
    pb = r.raw(3)
    person_bits = pb[0] | pb[1] << 8 | pb[2] << 16

    n_types = len(fmt.record_types)
    bat = {}
    m = _read_mask(r, fmt)
    for i in range(n_types):
        if m >> i & 1:
            bat[i] = BatRecord(*struct.unpack(BatRecord.FORMAT, r.raw(32)))
    bowl = {}
    m = _read_mask(r, fmt)
    for i in range(n_types):
        if m >> i & 1:
            bowl[i] = BowlRecord(*struct.unpack(BowlRecord.FORMAT, r.raw(12)))

    injury = r.byte()
    injury_extra = r.byte() if injury & 1 else None
    form = r.raw(12 * 11 + 2)
    intl = r.raw(18)
    pa, pbb, pc = r.dword(), r.dword(), r.byte()
    eng = batting = bowling = None
    if fmt.float_abilities:
        eng = r.dword()
        (batting,) = struct.unpack('<d', r.raw(8))
    ba, bb, bc = r.dword(), r.byte(), r.word()
    if fmt.float_abilities:
        (bowling,) = struct.unpack('<d', r.raw(8))
    return Player(key, team, ref, dob_status, dob, notes, first, sur, ini, wage, exp, mn,
                  person_bits, bat, bowl, injury, injury_extra, form, intl,
                  pa, pbb, pc, ba, bb, bc, national_id, eng, batting, bowling)


def _write_player(w, p, fmt=CLASSIC):
    w.byte(p.team); w.word(p.ref); w.dword(p.dob_status)
    w.raw(struct.pack('<d', p.dob))
    for s in (p.notes, p.first_name, p.surname, p.initials):
        w.string(s)
    w.dword(p.wage); w.dword(p.expected_wage); w.dword(p.minimum_wage)
    if fmt.nationality_dword:
        w.dword(p.national_id or 0)
    w.raw(bytes([p.person_bits & 0xFF, p.person_bits >> 8 & 0xFF, p.person_bits >> 16 & 0xFF]))
    _write_mask(w, sum(1 << i for i in p.batting_records), fmt)
    for i in sorted(p.batting_records):
        rec = p.batting_records[i]
        w.raw(struct.pack(BatRecord.FORMAT, *(getattr(rec, f) for f in rec.__dataclass_fields__)))
    _write_mask(w, sum(1 << i for i in p.bowling_records), fmt)
    for i in sorted(p.bowling_records):
        rec = p.bowling_records[i]
        w.raw(struct.pack(BowlRecord.FORMAT, *(getattr(rec, f) for f in rec.__dataclass_fields__)))
    w.byte(p.injury)
    if p.injury & 1:
        w.byte(p.injury_extra or 0)
    w.raw(p.form)
    w.raw(p.international_rating)
    w.dword(p.player_a); w.dword(p.player_b); w.byte(p.player_c)
    if fmt.float_abilities:
        w.dword(p.england_contracted or 0)
        w.raw(struct.pack('<d', p.batting_value or 0.0))
    w.dword(p.bowler_a); w.byte(p.bowler_b); w.word(p.bowler_c)
    if fmt.float_abilities:
        w.raw(struct.pack('<d', p.bowling_value or 0.0))


# ---------------------------------------------------------------- teams
TEAM_CLASS = 'CrTeam'
COACH_CLASS = 'CrCoach'
PADDING = b'\xff' * 5000   # CrDatabase::SerializeTemporary always appends this


@dataclass
class Team:
    """CrTeam: a domestic or national side's squad, selection and finances."""
    key: int
    grounds: List[int]            # home ground ids (dataP.db grounds)
    squad: List[int]              # player keys
    selected: List[int]           # player keys of the picked XI
    extra: List[int]              # fourth word list (empty in shipped data)
    history: List[List[str]]      # four lists of 2-char season results (positions / cup rounds)
    colour: int
    national: int                 # DWORD; non-zero = national side (has Test history)
    minor: int                    # DWORD; non-zero = minor team
    name_ref: int                 # team id in dataP.db
    year_founded: int
    coach_ref: int
    physio_ref: int
    captain: int                  # index into `selected` (0xFFFF = none)
    keeper: int
    opening_bowler1: int
    opening_bowler2: int
    yearly_income: int
    extra_income: int
    unknown_money: int
    youth_budget: int
    physio_budget: int
    coaching_budget: int
    retirements: int
    extra_costs: int
    last_history_year: int
    unknown_word: int
    country: int
    test_history: Optional[bytes] = None   # 144 bytes when `national`

    INDEX_FIELDS = ('captain', 'keeper', 'opening_bowler1', 'opening_bowler2')


@dataclass
class Coach:
    key: int
    type_byte: int     # CrCoachType
    packed: int        # high nibble / low nibble coach attributes


def _read_word_array(r):
    n = r.word()
    if n == 0xFFFF:
        n = r.dword()
    return [r.word() for _ in range(n)]


def _write_count(w, n):
    if n < 0xFFFF:
        w.word(n)
    else:
        w.word(0xFFFF); w.dword(n)


def _write_word_array(w, arr):
    _write_count(w, len(arr))
    for v in arr:
        w.word(v)


def _read_team(r, key, fmt=CLASSIC):
    grounds, squad, selected, extra = (_read_word_array(r) for _ in range(4))
    c = [r.byte() for _ in range(4)]           # counts for lists 0, 2, 1, 3
    counts = (c[0], c[2], c[1], c[3])
    history = [[r.raw(2).decode('latin-1') for _ in range(n)] for n in counts]
    colour = r.byte()
    national, minor = r.dword(), r.dword()
    words = [r.word() for _ in range(8)]
    money = [r.dword() for _ in range(6)]
    retirements, extra_costs = r.byte(), r.dword()
    last_year, unknown_word, country = r.word(), r.word(), r.word()
    hist = r.raw(fmt.test_history_size) if national else None
    return Team(key, grounds, squad, selected, extra, history, colour, national, minor,
                *words, *money, retirements, extra_costs, last_year, unknown_word, country, hist)


def _write_team(w, t, fmt=CLASSIC):
    for arr in (t.grounds, t.squad, t.selected, t.extra):
        _write_word_array(w, arr)
    h = t.history
    for n in (len(h[0]), len(h[2]), len(h[1]), len(h[3])):
        w.byte(n)
    for lst in h:
        for code in lst:
            b = code.encode('latin-1')
            if len(b) != 2:
                raise ValueError('history codes must be 2 characters: %r' % code)
            w.raw(b)
    w.byte(t.colour)
    w.dword(t.national); w.dword(t.minor)
    for v in (t.name_ref, t.year_founded, t.coach_ref, t.physio_ref,
              t.captain, t.keeper, t.opening_bowler1, t.opening_bowler2):
        w.word(v)
    for v in (t.yearly_income, t.extra_income, t.unknown_money,
              t.youth_budget, t.physio_budget, t.coaching_budget):
        w.dword(v)
    w.byte(t.retirements); w.dword(t.extra_costs)
    w.word(t.last_history_year); w.word(t.unknown_word); w.word(t.country)
    if t.national:
        if t.test_history is None or len(t.test_history) != fmt.test_history_size:
            raise ValueError('national team %d needs %d bytes of Test history'
                             % (t.key, fmt.test_history_size))
        w.raw(t.test_history)


# ---------------------------------------------------------------- file
@dataclass
class PlayerFile:
    lead: int
    players: List[Player]
    schema: int
    teams: List[Team] = field(default_factory=list)
    team_schema: int = 1
    coaches: List[Coach] = field(default_factory=list)
    coach_schema: int = 1
    padding: bytes = PADDING
    fmt: Format = CLASSIC

    @property
    def record_types(self):
        return list(self.fmt.record_types)

    # Abilities on the in-game scale, whichever way the format stores them.
    def batting(self, p):
        if self.fmt.float_abilities:
            return p.batting_value
        return BITFIELDS_BY_NAME['batting'].scaled(p['batting'])

    def bowling(self, p):
        if self.fmt.float_abilities:
            return p.bowling_value
        return BITFIELDS_BY_NAME['bowling'].scaled(p['bowling'])

    def duplicate_keys(self):
        seen, dups = set(), set()
        for p in self.players:
            (dups if p.key in seen else seen).add(p.key)
        return sorted(dups)

    @property
    def game_version(self):
        """2000 or 2002. The player layout is identical; only the nationality codes differ.
        ICC 2002 databases have national sides for Namibia/Canada/Netherlands and/or
        players using their codes; ICC 2000 databases have neither."""
        if self.fmt is FORMAT_2006:
            return 2006
        if getattr(self, '_version', None):
            return self._version
        extra = {1, 14, 15}
        if any(p['national_team'] in extra for p in self.players) or \
                any(t.national and t.name_ref in (75, 77, 78) for t in self.teams):
            return 2002
        return 2000

    @game_version.setter
    def game_version(self, v):
        if self.fmt is FORMAT_2006:
            if v != 2006:
                raise ValueError('this is an ICC 2006 database')
            return
        if v not in NATIONAL_TABLES:
            raise ValueError('unsupported game version %r' % v)
        self._version = v

    @property
    def national_teams(self):
        """Nationality choices as {value: team id}. Classic formats store a 4-bit code;
        ICC 2006 stores the team id itself (any national side, plus ids in use)."""
        if self.fmt.nationality_dword:
            ids = {t.name_ref for t in self.teams if t.national}
            ids |= {p.national_id for p in self.players if p.national_id}
            return {0: None, **{i: i for i in sorted(ids)}}
        return NATIONAL_TABLES[self.game_version]

    def national_team_id(self, p):
        if self.fmt.nationality_dword:
            return p.national_id or None
        return self.national_teams.get(p['national_team'])

    def set_national(self, p, value):
        """Set nationality from a key of `national_teams`."""
        value = int(value)
        if self.fmt.nationality_dword:
            p.national_id = value
        else:
            p['national_team'] = value

    def player_by_key(self, key):
        return next((p for p in self.players if p.key == key), None)

    def teams_with_player(self, key):
        return [t for t in self.teams if key in t.squad or key in t.selected or key in t.extra]

    # ------------------------------------------------------ add / remove
    def add_player(self, template, first_name, surname, initials='', team=None):
        """Add a new player cloned from `template` (abilities and form), with no career records."""
        import copy
        p = copy.deepcopy(template)
        p.key = p.ref = max(x.key for x in self.players) + 1
        if p.key > 0xFFFF:
            raise ValueError('no free player keys left')
        p.first_name, p.surname = first_name, surname
        p.initials = initials or ''.join(w[0] for w in (first_name + ' ' + surname).split() if w).upper()
        p.notes = ''
        p.batting_records, p.bowling_records = {}, {}
        p.injury, p.injury_extra = p.injury & ~1, None
        if team is not None:
            p.team = team
        self.players.append(p)
        if team:
            self.move_player(p.key, team)
        return p

    def remove_player(self, key):
        """Delete a player and scrub them from every squad / selection."""
        victims = [p for p in self.players if p.key == key]
        if not victims:
            raise KeyError('no player with key %d' % key)
        self.players = [p for p in self.players if p.key != key]
        for t in self.teams:
            self._drop(t, key, victims[0])

    def move_player(self, key, team_id):
        """Move a player to another domestic team's squad (team id = dataP.db id; 0 = none)."""
        p = self.player_by_key(key)
        if p is None:
            raise KeyError('no player with key %d' % key)
        for t in self.teams:
            if t.name_ref == p.team and not t.national:
                self._drop(t, key, p)
        p.team = team_id
        if team_id:
            dest = next((t for t in self.teams if t.name_ref == team_id and not t.national), None)
            if dest is not None and key not in dest.squad:
                dest.squad.append(key)


    def _drop(self, t, key, leaving):
        """Remove `key` from a team. A picked player is replaced in the same XI slot by
        an unpicked squad member (a keeper if they were the keeper), so the XI stays
        full and the captain / keeper / bowler roles stay valid."""
        t.squad = [k for k in t.squad if k != key]
        t.extra = [k for k in t.extra if k != key]
        if key not in t.selected:
            return
        pos = t.selected.index(key)
        spare = [k for k in t.squad if k not in t.selected and self.player_by_key(k)]
        if not spare and t.national:
            # National squads are just the XI: call up the best eligible player instead.
            eligible = [p for p in self.players
                        if self.national_team_id(p) == t.name_ref and p.key != key and p.key not in t.squad]
            if leaving['bat_type'] <= 1:        # opener / middle order: best batsman
                eligible.sort(key=lambda p: -self.batting(p))
            elif leaving['bat_type'] == 3:      # tail-ender: best bowler (lower is better)
                eligible.sort(key=lambda p: self.bowling(p))
            else:                                # all-rounder
                eligible.sort(key=lambda p: -(self.batting(p) - self.bowling(p)))
            spare = [p.key for p in eligible]
            if spare:
                t.squad.append(spare[0] if t.keeper != pos else
                               next((k for k in spare if self.player_by_key(k)['wicketkeeper']), spare[0]))
                spare = [t.squad[-1]]
        if t.keeper == pos:
            spare.sort(key=lambda k: -self.player_by_key(k)['wicketkeeper'])
        if spare:
            t.selected[pos] = spare[0]
            return
        t.selected.pop(pos)
        for f in Team.INDEX_FIELDS:
            v = getattr(t, f)
            if v == 0xFFFF:
                continue
            if v == pos:
                setattr(t, f, 0xFFFF)
            elif v > pos:
                setattr(t, f, v - 1)
        if t.captain == 0xFFFF and t.selected:
            t.captain = 0


def _read_map(r, cls, read_value):
    n = r.word()
    if n == 0xFFFF:
        n = r.dword()
    out, schema = [], None
    for _ in range(n):
        key = r.word()
        s = r.object_tag(cls)
        if s is not None:
            schema = s
        out.append(read_value(r, key))
    return out, schema


def parse(data, fmt=CLASSIC):
    r = Reader(data)
    lead = r.dword()
    players, schema = _read_map(r, PLAYER_CLASS, lambda r, k: _read_player(r, k, fmt))
    teams, team_schema = _read_map(r, TEAM_CLASS, lambda r, k: _read_team(r, k, fmt))
    coaches, coach_schema = _read_map(r, COACH_CLASS, lambda r, k: Coach(k, r.byte(), r.byte()))
    padding = data[r.pos:]
    return PlayerFile(lead, players, schema, teams, team_schema or 1, coaches, coach_schema or 1,
                      padding, fmt)


def serialize(pf):
    w = Writer()
    w.dword(pf.lead)
    for items, cls, schema, write in (
            (pf.players, PLAYER_CLASS, pf.schema, lambda w, p: _write_player(w, p, pf.fmt)),
            (pf.teams, TEAM_CLASS, pf.team_schema, lambda w, t: _write_team(w, t, pf.fmt)),
            (pf.coaches, COACH_CLASS, pf.coach_schema, lambda w, c: (w.byte(c.type_byte), w.byte(c.packed)))):
        _write_count(w, len(items))
        for it in items:
            w.word(it.key)
            w.object_tag(cls, schema)
            write(w, it)
    w.raw(pf.padding)
    return w.getvalue()


def load(path):
    """Load a player file, detecting its format (ICC 2000/2002 or ICC 2006)."""
    errors = []
    for fmt in FORMATS:
        try:
            pf = parse(read_db(path, fmt.seed_with_length), fmt)
        except (ValueError, EOFError, IndexError, struct.error, UnicodeDecodeError) as e:
            errors.append('%s: %s' % (fmt.name, e))
            continue
        if pf.padding == PADDING:
            return pf
        errors.append('%s: unexpected trailing data' % fmt.name)
    raise ValueError('not a recognised ICC player file (%s)' % '; '.join(errors))


def save(pf, path):
    write_db(path, serialize(pf), pf.fmt.seed_with_length)
