"""Parser/serializer for the ICC 2002 team file (dataP.db).

Decrypted layout (all little-endian, strings are MFC CStrings):
    WORD n, n x WORD                team ids
    WORD n, n x CString             full names      ("Derbyshire")
    WORD n, n x CString             short names     ("Derbys")
    WORD n, n x CString             abbreviations   ("Dby")
    WORD                            unknown (always equals the team count so far)
    WORD n, n x (WORD id, CrGround) grounds
    record blocks until end of file (18 counties + 9 Test nations)
"""
from dataclasses import dataclass, field
from typing import List

from .archive import Reader, Writer
from .crypto import read_db, write_db

GROUND_CLASS = 'CrGround'


@dataclass
class Team:
    id: int
    name: str
    short_name: str
    abbrev: str


@dataclass
class Ground:
    id: int
    name: str          # display name, e.g. "Headingley"
    full_name: str     # e.g. "Headingley Cricket Ground"
    address: str       # e.g. "St Michael's Lane"
    town: str          # e.g. "Leeds"
    description: str


@dataclass
class Partnership:
    batter1: str
    batter2: str
    opponent: int      # team id
    year: int
    runs: int
    not_out: bool


@dataclass
class TeamTotal:
    runs: int
    opponent: int
    year: int


@dataclass
class SeasonBest:
    value: int         # runs or wickets
    player: str
    year: int


@dataclass
class BestBowling:
    player: str
    wickets: int
    runs: int
    opponent: int
    venue: str
    year: int


@dataclass
class HighScore:
    runs: int
    player: str
    opponent: int
    venue: str
    year: int


@dataclass
class TeamRecords:
    lead: bytes                      # 2 bytes, always 01 01 so far
    partnerships: List[Partnership]  # wickets 1..10
    unknown: bytes                   # 5 bytes, meaning not yet known
    highest_total: TeamTotal
    lowest_total: TeamTotal
    most_runs_season: SeasonBest
    most_wickets_season: SeasonBest
    best_bowling_match: BestBowling
    best_bowling_innings: BestBowling
    highest_score: HighScore


@dataclass
class TeamFile:
    teams: List[Team]
    unknown_word: int
    grounds: List[Ground]
    ground_schema: int
    records: List[TeamRecords] = field(default_factory=list)
    seed_with_length: bool = False   # ICC 2006 encryption variant


# ---------------------------------------------------------------- parsing

def _read_best_bowling(r):
    return BestBowling(r.string(), r.byte(), r.word(), r.word(), r.string(), r.word())


def _read_records(r):
    lead = r.raw(2)
    partnerships = []
    for _ in range(10):
        b1, b2, opp = r.string(), r.string(), r.word()
        packed, low = r.word(), r.byte()
        partnerships.append(Partnership(b1, b2, opp, packed >> 4,
                                        (packed & 0xF) << 7 | low >> 1, bool(low & 1)))
    unknown = r.raw(5)
    highest = TeamTotal(r.word(), r.word(), r.word())
    lowest = TeamTotal(r.word(), r.word(), r.word())
    runs = SeasonBest(r.word(), r.string(), r.word())
    wkts = SeasonBest(r.word(), r.string(), r.word())
    bm = _read_best_bowling(r)
    bi = _read_best_bowling(r)
    hs = HighScore(r.word(), r.string(), r.word(), r.string(), r.word())
    return TeamRecords(lead, partnerships, unknown, highest, lowest, runs, wkts, bm, bi, hs)


def parse(data):
    r = Reader(data)
    ids = [r.word() for _ in range(r.word())]
    names = [r.string() for _ in range(r.word())]
    shorts = [r.string() for _ in range(r.word())]
    abbrevs = [r.string() for _ in range(r.word())]
    if not len(ids) == len(names) == len(shorts) == len(abbrevs):
        raise ValueError('team table lengths differ')
    teams = [Team(*t) for t in zip(ids, names, shorts, abbrevs)]

    unknown_word = r.word()
    grounds = []
    schema = None
    for _ in range(r.word()):
        gid = r.word()
        s = r.object_tag(GROUND_CLASS)
        if s is not None:
            schema = s
        grounds.append(Ground(gid, r.string(), r.string(), r.string(), r.string(), r.string()))

    records = []
    while not r.eof():
        records.append(_read_records(r))
    return TeamFile(teams, unknown_word, grounds, schema, records)


# ------------------------------------------------------------ serializing

def _write_best_bowling(w, b):
    w.string(b.player); w.byte(b.wickets); w.word(b.runs)
    w.word(b.opponent); w.string(b.venue); w.word(b.year)


def _write_records(w, rec):
    w.raw(rec.lead)
    for p in rec.partnerships:
        w.string(p.batter1); w.string(p.batter2); w.word(p.opponent)
        w.word(p.year << 4 | p.runs >> 7)
        w.byte((p.runs & 0x7F) << 1 | int(p.not_out))
    w.raw(rec.unknown)
    for t in (rec.highest_total, rec.lowest_total):
        w.word(t.runs); w.word(t.opponent); w.word(t.year)
    for s in (rec.most_runs_season, rec.most_wickets_season):
        w.word(s.value); w.string(s.player); w.word(s.year)
    _write_best_bowling(w, rec.best_bowling_match)
    _write_best_bowling(w, rec.best_bowling_innings)
    h = rec.highest_score
    w.word(h.runs); w.string(h.player); w.word(h.opponent); w.string(h.venue); w.word(h.year)


def serialize(tf):
    w = Writer()
    n = len(tf.teams)
    w.word(n)
    for t in tf.teams:
        w.word(t.id)
    for attr in ('name', 'short_name', 'abbrev'):
        w.word(n)
        for t in tf.teams:
            w.string(getattr(t, attr))
    w.word(tf.unknown_word)
    w.word(len(tf.grounds))
    for g in tf.grounds:
        w.word(g.id)
        w.object_tag(GROUND_CLASS, tf.ground_schema)
        for s in (g.name, g.full_name, g.address, g.town, g.description):
            w.string(s)
    for rec in tf.records:
        _write_records(w, rec)
    return w.getvalue()


def load(path):
    """Load a team file; tries the ICC 2000/2002 and the ICC 2006 encryption seeds."""
    errors = []
    for seed_with_length in (False, True):
        try:
            tf = parse(read_db(path, seed_with_length))
        except (ValueError, EOFError, IndexError, UnicodeDecodeError, Exception) as e:
            errors.append(str(e))
            continue
        tf.seed_with_length = seed_with_length
        return tf
    raise ValueError('not a recognised ICC team file (%s)' % '; '.join(errors))


def save(tf, path):
    write_db(path, serialize(tf), tf.seed_with_length)
