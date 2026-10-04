"""Editor backend for the ICC 2 (1999) single-file database.

Exposes the same JSON API as server.Store so the web page works unchanged, mapping
ICC 2's float abilities, squads and record books onto the shapes the page expects.
ACCStore reuses it for the ICC 1 engine games (accfile: ICC 1998, Australian Cricket Captain).
"""
import copy
import dataclasses
import datetime
import os
import shutil
import struct
import threading
from pathlib import Path

from .. import accfile, icc2file
from .fixtures import FixtureSet, FixtureStoreMixin, find_fixture_dir
from ..icc2file import RECORD_NAMES

ROLE_FIELDS = ('captain', 'keeper', 'opening_bowler1', 'opening_bowler2')
NONE_ROLE = 0xFFFFFFFF
WAGES = ('wage', 'expected_wage', 'minimum_wage')


class SaveError(Exception):
    pass


# Team values (roles are indexes into the picked XI) live in Team.fixed; see icc2file.TEAM_FIXED.
def _roles(t):
    return [t.value(f) for f in ('captain', 'keeper', 'opening_bowler1', 'opening_bowler2')]


def _set_roles(t, roles):
    for f, v in zip(('captain', 'keeper', 'opening_bowler1', 'opening_bowler2'), roles):
        t.set_value(f, v)


def open_database(path):
    """Store for a single-file database.db: ICC 2 (1999), ICC 1998 or Australian Cricket Captain."""
    try:
        return ICC2Store(path)
    except (ValueError, EOFError):
        return ACCStore(path)


class ICC2Store(FixtureStoreMixin):
    F = icc2file             # file-format module
    game_label = '2 (1999)'
    title = None             # page title; default "ICC <game_version> Editor"
    team_money = ('yearly_income', 'extra_income', 'youth_budget', 'physio_budget', 'coaching_budget', 'extra_costs')

    def __init__(self, path):
        self.lock = threading.Lock()
        self.path = Path(path)
        self.db = self.F.load(self.path)
        self.dirty = False
        self._load_fixtures()

    def _load_fixtures(self):
        # ICC 1998 / ACC keep their .fxt files next to database.db (ICC 2 has none)
        fx_dir = find_fixture_dir(self.path.parent)
        self.fx = FixtureSet(fx_dir) if fx_dir else None

    def _venue_info(self):
        gname = {g.id: g.name for g in self.db.grounds.items}
        grounds = {t.key: [gname.get(x, '#%d' % x) for x in t.lists[0]] for t in self.db.teams.items}
        return {'team_grounds': grounds, 'tests': {t.key for t in self.db.teams.items if t.value('national')},
                'grounds': gname}

    # ------------------------------------------------------------ helpers
    @property
    def players(self):
        return self.db.players.items

    def team_names(self):
        return {t.id: t.name for t in self.db.team_names}

    def national_ids(self):
        ids = {p.nation for p in self.players if p.nation}
        return sorted(ids | {t.key for t in self.db.teams.items if t.value('national')})

    def team_obj(self, team_id):
        return next((t for t in self.db.teams.items if t.key == team_id), None)

    # -------------------------------------------------------------- meta
    def meta(self):
        names = self.team_names()
        fields = [dict(name=f.name, label=f.label, group=f.group, kind='float' if f.kind == 'f' else 'int',
                       max=1 if f.kind == 'b' else (None if f.kind == 'f' else 65535), lo=None, hi=None)
                  for f in self.F.FIELDS]
        fields += [dict(name='bat_type', label='Batting role', group='Role', kind='int', max=7, lo=None, hi=None),
                   dict(name='bowler_type', label='Bowler type', group='Bowling', kind='int', max=7, lo=None, hi=None)]
        meta = {
            'kind': 'icc2', 'game_version': self.game_label,
            'players_path': str(self.path), 'teams_path': str(self.path),
            'dirty': self.dirty, 'duplicate_keys': [],
            'record_types': self.F.RECORD_TYPES, 'career_records': True, 'float_abilities': False,
            'national_teams': {'0': 'None', **{str(i): names.get(i, '#%d' % i) for i in self.national_ids()}},
            'bowler_types': self.F.BOWLER_TYPES, 'bat_types': self.F.BAT_TYPES,
            'fields': fields,
            'fixtures_dir': str(self.fx.folder) if self.fx else None,
        }
        if self.title:
            meta['title'] = self.title
        return meta

    # ----------------------------------------------------------- players
    def player_summary(self, i, p, names):
        return {'index': i, 'key': p.key, 'name': p.person.name, 'initials': p.person.initials,
                'team': p.person.team, 'team_name': names.get(p.person.team, ''),
                'nation': names.get(p.person.nation, '') if p.person.nation else '',
                'batting': round(p.get('batting')), 'bowling': round(p.get('bowling')),
                'wicketkeeper': p.get('wicketkeeper')}

    def players_list(self):
        names = self.team_names()
        return [self.player_summary(i, p, names) for i, p in enumerate(self.players)]

    def player_json(self, i):
        p = self.players[i]
        pe = p.person
        fields = {f.name: (round(p.get(f.name), 3) if f.kind == 'f' else p.get(f.name)) for f in self.F.FIELDS}
        fields.update(bat_type=p.bat_type, bowler_type=p.bowler_type)
        return {
            'index': i, 'key': p.key, 'ref': pe.ref, 'team': pe.team,
            'first_name': pe.first_name, 'surname': pe.surname, 'initials': pe.initials, 'notes': pe.notes,
            'birthday': pe.birthday.isoformat(),
            **{k: round(p.wage(k)) for k in WAGES},
            'injured': False, 'nationality': pe.nation,
            'batting_value': None, 'bowling_value': None, 'england_contracted': None,
            'fields': fields,
            'batting_records': self._bat_rows(p), 'bowling_records': self._bowl_rows(p),
        }

    # Career records: a row is shown when any of its values is non-zero. Batting rows
    # also carry the fielding record (catches / stumpings) of the same type.
    def _bat_rows(self, p):
        rows = {}
        for t in range(len(self.F.RECORD_TYPES)):
            b = self.F.get_record(p, 'batting', t) or {}
            f = self.F.get_record(p, 'fielding', t) or {}
            if any(v for k, v in b.items() if k != 'unknown') or any(f.values()):
                row = {k: v for k, v in b.items() if k != 'unknown'}
                row.update(caught=f.get('caught', 0), stumped=f.get('stumped', 0))
                rows[str(t)] = row
        return rows

    def _bowl_rows(self, p):
        rows = {}
        for t in range(len(self.F.RECORD_TYPES)):
            b = self.F.get_record(p, 'bowling', t)
            if b and any(v for k, v in b.items() if k != 'unknown'):
                rows[str(t)] = {k: v for k, v in b.items() if k != 'unknown'}
        return rows

    def update_player(self, i, data):
        p = self.players[i]
        pe = p.person
        for k in ('first_name', 'surname', 'initials', 'notes'):
            if k in data and getattr(pe, k) is not None:   # ACC has no notes
                v = str(data[k]); v.encode('latin-1')
                setattr(pe, k, v)
        if 'birthday' in data:
            pe.birthday = datetime.date.fromisoformat(data['birthday'])
        for k in WAGES:
            if k in data:
                p.set_wage(k, max(0, float(data[k])))
        if 'nationality' in data:
            n = int(data['nationality'])
            if n and n not in self.national_ids():
                raise ValueError('unknown nationality')
            pe.nation = n
        for name, v in data.get('fields', {}).items():
            if name == 'bat_type':
                p.bat_type = int(v) & 7
            elif name == 'bowler_type':
                p.bowler_type = int(v) & 7
            elif name in self.F.FIELDS_BY_NAME:
                p.set(name, v)
            else:
                raise ValueError('unknown field %s' % name)
        F = self.F
        if 'batting_records' in data:
            recs = {int(k): v for k, v in data['batting_records'].items()}
            for t in range(len(F.RECORD_TYPES)):
                if t in recs:
                    F.set_record(p, 'batting', t, recs[t])
                    F.set_record(p, 'fielding', t, {k: recs[t][k] for k in ('caught', 'stumped') if k in recs[t]})
                else:
                    F.set_record(p, 'batting', t, None)
                    F.set_record(p, 'fielding', t, None)
        if 'bowling_records' in data:
            recs = {int(k): v for k, v in data['bowling_records'].items()}
            for t in range(len(F.RECORD_TYPES)):
                F.set_record(p, 'bowling', t, recs.get(t))
        if 'team' in data and int(data['team']) != pe.team:
            self.move_player(p.key, int(data['team']))
        self.dirty = True
        return self.player_json(i)

    def add_player(self, data):
        template = self.players[int(data.get('template', 0))]
        first, sur = str(data.get('first_name', '')).strip(), str(data.get('surname', '')).strip()
        if not sur:
            raise ValueError('surname is required')
        (first + sur).encode('latin-1')
        p = copy.deepcopy(template)
        p.key = max(x.key for x in self.players) + 1
        if p.key > 0xFFFF:
            raise ValueError('no free player keys left')
        pe = p.person
        pe.ref = p.key
        pe.first_name, pe.surname = first, sur
        pe.initials = str(data.get('initials', '')).strip() or ''.join(w[0] for w in (first + ' ' + sur).split()).upper()
        if pe.notes is not None:
            pe.notes = ''
        # start with empty career records (keep each block's trailing DWORD)
        p.batting_records = bytes(len(p.batting_records) - 4) + p.batting_records[-4:]
        p.bowling_records = bytes(len(p.bowling_records) - 4) + p.bowling_records[-4:]
        p.fielding_records = bytes(len(p.fielding_records) - 4) + p.fielding_records[-4:]
        pe.team = 0
        self.players.append(p)
        self.db.players.index = sorted(self.db.players.index + [p.key])
        self.db.players.last_id = max(self.db.players.last_id, p.key)
        if int(data.get('team', 0)):
            self.move_player(p.key, int(data['team']))
        self.dirty = True
        return self.player_json(self.players.index(p))

    def delete_player(self, i):
        p = self.players[i]
        for t in self.db.teams.items:
            self._drop(t, p.key, p)
        self.db.players.items = [x for x in self.players if x.key != p.key]
        self.db.players.index = [k for k in self.db.players.index if k != p.key]
        self.dirty = True
        return {'deleted': p.person.name}

    def move_player(self, key, team_id):
        p = self.db.player_by_key(key)
        old = self.team_obj(p.person.team)
        if old is not None:
            self._drop(old, key, p)
        p.person.team = team_id
        new = self.team_obj(team_id)
        if new is not None and key not in new.squad:
            new.squad.append(key)

    def _drop(self, t, key, leaving):
        """Remove a player from a team; a picked player's XI slot is refilled from the squad."""
        t.lists[1][:] = [k for k in t.squad if k != key]
        for j in (3, 4):
            t.lists[j][:] = [k for k in t.lists[j] if k != key]
        if key not in t.selected:
            return
        pos = t.selected.index(key)
        roles = _roles(t)
        spare = [k for k in t.squad if k not in t.selected and self.db.player_by_key(k)]
        if roles[1] == pos:
            spare.sort(key=lambda k: -self.db.player_by_key(k).get('wicketkeeper'))
        if spare:
            t.selected[pos] = spare[0]
            return
        t.selected.pop(pos)
        roles = [NONE_ROLE if r == pos else (r - 1 if r != NONE_ROLE and r > pos else r) for r in roles]
        if roles[0] == NONE_ROLE and t.selected:
            roles[0] = 0
        _set_roles(t, roles)

    # ------------------------------------------------------------- teams
    def teams_json(self):
        counts = {}
        for p in self.players:
            counts[p.person.team] = counts.get(p.person.team, 0) + 1
        clubs = {t.key: t for t in self.db.teams.items}
        nat = set(self.national_ids())
        return [dict(dataclasses.asdict(t), index=i, players=counts.get(t.id, 0),
                     squad=len(clubs[t.id].squad) if t.id in clubs and clubs[t.id].squad else None,
                     national=t.id in nat)
                for i, t in enumerate(self.db.team_names)]

    def team_detail(self, i):
        tn = self.db.team_names[i]
        d = dict(dataclasses.asdict(tn), index=i, club=None)
        t = self.team_obj(tn.id)
        if t is None or not (t.squad or t.selected):
            return d

        def row(k):
            p = self.db.player_by_key(k)
            if p is None:
                return {'key': k, 'index': None, 'name': '(missing player %d)' % k}
            return {'key': k, 'index': self.players.index(p), 'name': p.person.name,
                    'wicketkeeper': p.get('wicketkeeper'),
                    'batting': round(p.get('batting')), 'bowling': round(p.get('bowling'))}
        roles = _roles(t)
        d['club'] = {
            'national': bool(t.value('national')), 'minor': bool(t.has_value('minor') and t.value('minor')),
            'squad': [row(k) for k in t.squad], 'selected': [row(k) for k in t.selected],
            'roles': {f: (None if r >= len(t.selected) else r) for f, r in zip(ROLE_FIELDS, roles)},
            'money': {k: t.value(k) for k in self.team_money},
            'year_founded': t.value('year_founded'), 'colour': t.colour, 'history': t.history,
        }
        return d

    def update_team_detail(self, i, data):
        tn = self.db.team_names[i]
        for k in ('name', 'short_name', 'abbrev'):
            if k in data:
                str(data[k]).encode('latin-1')
                setattr(tn, k, str(data[k]))
        t = self.team_obj(tn.id)
        club = data.get('club')
        if club and t is not None:
            if 'squad' in club:
                keys = [int(k) for k in club['squad']]
                if any(self.db.player_by_key(k) is None for k in keys):
                    raise ValueError('unknown player in squad')
                for k in [k for k in t.squad if k not in keys]:
                    self.move_player(k, 0)
                for k in keys:
                    if k not in t.squad:
                        self.move_player(k, tn.id)
                t.lists[1][:] = [k for k in keys if k in t.squad] + [k for k in t.squad if k not in keys]
            if 'selected' in club:
                sel = [int(k) for k in club['selected']]
                if len(sel) != len(set(sel)):
                    raise ValueError('a player is picked twice')
                if any(k not in t.squad for k in sel):
                    raise ValueError('picked players must be in the squad')
                t.lists[2][:] = sel
            if 'roles' in club:
                roles = _roles(t)
                for j, f in enumerate(ROLE_FIELDS):
                    if f in club['roles']:
                        v = club['roles'][f]
                        v = NONE_ROLE if v is None or v == '' else int(v)
                        if v != NONE_ROLE and not 0 <= v < len(t.selected):
                            raise ValueError('%s must point at a picked player' % f)
                        roles[j] = v
                _set_roles(t, roles)
            for k, v in club.get('money', {}).items():
                if k in self.team_money:
                    t.set_value(k, max(0, int(v)))
            if 'year_founded' in club:
                t.set_value('year_founded', int(club['year_founded']))
            if 'colour' in club:
                t.colour = int(club['colour']) & 0xFFFFFFFF
            roles = _roles(t)   # keep roles valid if the XI shrank
            _set_roles(t, [NONE_ROLE if r != NONE_ROLE and r >= len(t.selected) else r for r in roles])
        self.dirty = True
        return self.team_detail(i)

    # ----------------------------------------------------------- grounds
    def grounds_list(self):
        return [dict(index=i, id=g.id, name=g.name, full_name=g.full_name, address=g.address,
                     town=g.town, description=g.description) for i, g in enumerate(self.db.grounds.items)]

    def update_ground(self, i, data):
        g = self.db.grounds.items[i]
        for k in ('name', 'full_name', 'address', 'town', 'description'):
            if k in data:
                str(data[k]).encode('latin-1')
                setattr(g, k, str(data[k]))
        self.dirty = True
        return self.grounds_list()[i]

    # ----------------------------------------------------------- records
    # The page's Club records screen edits the first-class record book of each team.
    def _book_json(self, idx):
        ti, bi = divmod(idx, 2)
        t = self.db.teams.items[ti]
        book = t.records[bi]
        v = dict(zip(RECORD_NAMES, book.values))
        name = self.team_names().get(t.key, 'Team %d' % t.key)
        return {
            'index': idx, 'owner': name + (' (one-day)' if bi else ''),
            'partnerships': [dict(batter1=p.batter1, batter2=p.batter2, opponent=p.opponent, year=p.year,
                                  runs=p.runs, not_out=bool(p.not_out)) for p in book.partnerships],
            'highest_total': dict(runs=v['highest_total'], opponent=v['highest_total_opp'], year=v['highest_total_year']),
            'lowest_total': dict(runs=v['lowest_total'], opponent=v['lowest_total_opp'], year=v['lowest_total_year']),
            'most_runs_season': dict(value=v['season_runs'], player=v['season_runs_player'], year=v['season_runs_year']),
            'most_wickets_season': dict(value=v['season_wickets'], player=v['season_wickets_player'], year=v['season_wickets_year']),
            'best_bowling_match': dict(player=v['best_match_player'], wickets=v['best_match_wickets'], runs=v['best_match_runs'],
                                       opponent=v['best_match_opp'], venue=v['best_match_venue'], year=v['best_match_year']),
            'best_bowling_innings': dict(player=v['best_innings_player'], wickets=v['best_innings_wickets'], runs=v['best_innings_runs'],
                                         opponent=v['best_innings_opp'], venue=v['best_innings_venue'], year=v['best_innings_year']),
            'highest_score': dict(runs=v['highest_score'], player=v['highest_score_player'], opponent=v['highest_score_opp'],
                                  venue=v['highest_score_venue'], year=v['highest_score_year']),
        }

    def records_list(self):
        # every team that has a first-class record book, followed by its one-day book
        out = []
        for i, t in enumerate(self.db.teams.items):
            if any(p.runs for p in t.records[0].partnerships) or t.records[0].values[0]:
                out += [self._book_json(2 * i), self._book_json(2 * i + 1)]
        return out

    def update_records(self, idx, data):
        ti, bi = divmod(idx, 2)
        t = self.db.teams.items[ti]
        book = t.records[bi]
        for j, p in enumerate(data.get('partnerships', [])[:10]):
            bp = book.partnerships[j]
            for k in ('batter1', 'batter2'):
                if k in p:
                    str(p[k]).encode('latin-1'); setattr(bp, k, str(p[k]))
            for k in ('opponent', 'year', 'runs'):
                if k in p:
                    setattr(bp, k, int(p[k]))
            if 'not_out' in p:
                bp.not_out = 1 if p['not_out'] else 0
        vals = dict(zip(RECORD_NAMES, book.values))
        mapping = {
            'highest_total': {'runs': 'highest_total', 'opponent': 'highest_total_opp', 'year': 'highest_total_year'},
            'lowest_total': {'runs': 'lowest_total', 'opponent': 'lowest_total_opp', 'year': 'lowest_total_year'},
            'most_runs_season': {'value': 'season_runs', 'player': 'season_runs_player', 'year': 'season_runs_year'},
            'most_wickets_season': {'value': 'season_wickets', 'player': 'season_wickets_player', 'year': 'season_wickets_year'},
            'best_bowling_match': {'player': 'best_match_player', 'wickets': 'best_match_wickets', 'runs': 'best_match_runs',
                                   'opponent': 'best_match_opp', 'venue': 'best_match_venue', 'year': 'best_match_year'},
            'best_bowling_innings': {'player': 'best_innings_player', 'wickets': 'best_innings_wickets', 'runs': 'best_innings_runs',
                                     'opponent': 'best_innings_opp', 'venue': 'best_innings_venue', 'year': 'best_innings_year'},
            'highest_score': {'runs': 'highest_score', 'player': 'highest_score_player', 'opponent': 'highest_score_opp',
                              'venue': 'highest_score_venue', 'year': 'highest_score_year'},
        }
        for section, fields in mapping.items():
            for k, name in fields.items():
                if k in data.get(section, {}):
                    v = data[section][k]
                    if isinstance(vals[name], str):
                        v = str(v); v.encode('latin-1')
                    else:
                        v = int(v)
                    vals[name] = v
        book.values = [vals[n] for n in RECORD_NAMES]
        self.F.serialize(self.db)   # validate that everything still encodes
        self.dirty = True
        return self._book_json(idx)

    # -------------------------------------------------------------- save
    def save(self):
        F = self.F
        data = F.serialize(self.db)
        if F.serialize(F.parse(data)) != data:
            raise SaveError('re-read check failed')
        fx_out = self.fx.build() if self.fx else []
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        fx_saved, backups = self.fx.write(fx_out, stamp) if fx_out else ([], [])
        if self.path.exists():
            b = self.path.with_name(self.path.name + '.bak-' + stamp)
            shutil.copy2(self.path, b)
            backups.append(str(b))
        tmp = self.path.with_name(self.path.name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, self.path)
        if F.serialize(F.load(self.path)) != data:
            raise SaveError('file on disk does not match after saving; restore from %s' % ', '.join(backups))
        self.dirty = False
        return {'saved': [str(self.path)] + fx_saved, 'backups': backups}


class ACCStore(ICC2Store):
    """ICC 1 engine games - International Cricket Captain (1998) and Australian Cricket Captain:
    ICC 2's outline with fewer career records and no club record books. F is the detected accfile.Format."""
    team_money = accfile.TEAM_MONEY

    def __init__(self, path):
        self.lock = threading.Lock()
        self.path = Path(path)
        self.db = accfile.load(self.path)
        self.F = self.db.fmt
        self.game_label = self.F.name
        self.title = self.F.title
        self.dirty = False
        self._load_fixtures()

    def meta(self):
        return dict(super().meta(), club_records=False,
                    bat_columns=accfile.BAT_COLUMNS, bowl_columns=accfile.BOWL_COLUMNS)

    def records_list(self):
        return []
