"""Local web editor for ICC 2002 databases.

    python3 -m icc.editor            # edits Original DB/2002/dataT.db + dataP.db
    python3 -m icc.editor --players path/dataT.db --teams path/dataP.db

Then open http://localhost:8002. Saving writes both files back in the game's
encrypted format, after copying the previous versions to *.bak-<timestamp>.
"""
import argparse
import dataclasses
import datetime
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import playerfile, teamfile
from ..crypto import decode, encode
from ..playerfile import BITFIELDS, BITFIELDS_BY_NAME, BatRecord, BowlRecord
from . import __version__, app
from .fixtures import FixtureSet, SaveError, find_fixture_dir

STATIC = Path(__file__).with_name('static')
STATIC_FILES = {'icon.png': 'image/png', 'help.html': 'text/html; charset=utf-8'}

TEAM_MONEY = ['yearly_income', 'extra_income', 'youth_budget', 'physio_budget', 'coaching_budget', 'extra_costs']

BAT_FIELDS = ['matches', 'innings', 'not_outs', 'runs', 'highest', 'highest_not_out', 'centuries',
              'fifties', 'balls', 'caught', 'stumped', 'debut', 'unknown']
BOWL_FIELDS = ['balls', 'runs', 'wickets', 'best_wickets', 'best_runs', 'five_wickets',
               'ten_wickets', 'unknown']


class Store:
    """In-memory copy of both files plus dirty tracking."""

    def __init__(self, players_path, teams_path, game=None, fixtures=None):
        self.lock = threading.Lock()
        self.players_path = Path(players_path)
        self.teams_path = Path(teams_path)
        self.pf = playerfile.load(self.players_path)
        if game:
            self.pf.game_version = game
        self.game_label = str(self.pf.game_version)
        self.tf = teamfile.load(self.teams_path)
        self.dirty = False
        # Fixture files: an explicit folder, else *.fxt next to the database (or in its Fxt/).
        fx_dir = find_fixture_dir(fixtures) if fixtures else find_fixture_dir(self.players_path.parent)
        if fixtures and fx_dir is None:
            raise SystemExit('no .fxt files in %s' % fixtures)
        self.fx = FixtureSet(fx_dir) if fx_dir else None

    # ---------------------------------------------------- listing helpers
    def meta(self):
        return meta(self)

    def players_list(self):
        names = self.team_names()
        return [self.player_summary(i, p, names) for i, p in enumerate(self.pf.players)]

    def grounds_list(self):
        return [dict(dataclasses.asdict(g), index=i) for i, g in enumerate(self.tf.grounds)]

    def records_list(self):
        owners = meta_record_owners()
        return [dict(records_json(r, i), owner=owners[i] if i < len(owners) else 'Record book %d' % (i + 1))
                for i, r in enumerate(self.tf.records)]

    def team_names(self):
        return {t.id: t.name for t in self.tf.teams}

    # ------------------------------------------------------------ players
    def player_summary(self, i, p, names):
        return {
            'index': i, 'key': p.key, 'name': p.name, 'initials': p.initials,
            'team': p.team, 'team_name': names.get(p.team, ''),
            'nation': names.get(self.pf.national_team_id(p), '') if self.pf.national_team_id(p) else '',
            'batting': round(self.pf.batting(p)),
            'bowling': round(self.pf.bowling(p)),
            'wicketkeeper': p['wicketkeeper'],
        }

    def player_json(self, i):
        p = self.pf.players[i]
        return {
            'index': i, 'key': p.key, 'ref': p.ref, 'team': p.team,
            'first_name': p.first_name, 'surname': p.surname, 'initials': p.initials, 'notes': p.notes,
            'birthday': p.birthday.isoformat(),
            'wage': p.wage * 500, 'expected_wage': p.expected_wage * 500, 'minimum_wage': p.minimum_wage * 500,
            'injured': p.injured,
            'nationality': (p.national_id or 0) if self.pf.fmt.nationality_dword else p['national_team'],
            'batting_value': round(self.pf.batting(p), 2),
            'bowling_value': round(self.pf.bowling(p), 2),
            'england_contracted': p.england_contracted,
            'fields': {f.name: p[f.name] for f in editable_fields(self.pf)},
            'batting_records': {str(k): {f: getattr(r, f) for f in BAT_FIELDS}
                                for k, r in sorted(p.batting_records.items())},
            'bowling_records': {str(k): {f: getattr(r, f) for f in BOWL_FIELDS}
                                for k, r in sorted(p.bowling_records.items())},
        }

    def update_player(self, i, data):
        p = self.pf.players[i]
        for k in ('first_name', 'surname', 'initials', 'notes'):
            if k in data:
                v = str(data[k])
                v.encode('latin-1')  # the game only stores Latin-1 text
                setattr(p, k, v)
        if 'team' in data:
            t = int(data['team'])
            if not 0 <= t <= 255:
                raise ValueError('team id must be 0..255')
            if t != p.team:
                self.pf.move_player(p.key, t)
        if 'birthday' in data:
            p.birthday = datetime.date.fromisoformat(data['birthday'])
        for k in ('wage', 'expected_wage', 'minimum_wage'):
            if k in data:
                setattr(p, k, max(0, int(data[k])) // 500)
        allowed = {f.name for f in editable_fields(self.pf)}
        for name, v in data.get('fields', {}).items():
            if name not in allowed:
                raise ValueError('%s cannot be edited for this game version' % name)
            p[name] = int(v)
        if 'nationality' in data:
            if int(data['nationality']) not in self.pf.national_teams:
                raise ValueError('unknown nationality for this game version')
            self.pf.set_national(p, data['nationality'])
        if self.pf.fmt.float_abilities:
            for k in ('batting_value', 'bowling_value'):
                if k in data:
                    v = float(data[k])
                    if not 0 <= v <= 400:
                        raise ValueError('%s must be between 0 and 400' % k.split('_')[0])
                    setattr(p, k, v)
            if 'england_contracted' in data:
                p.england_contracted = 1 if data['england_contracted'] else 0
        if 'batting_records' in data:
            recs = {}
            for k, vals in data['batting_records'].items():
                if not 0 <= int(k) < len(self.pf.record_types):
                    raise ValueError('unknown record type %s' % k)
                r = p.batting_records.get(int(k)) or BatRecord(*[0] * 13)
                for f in BAT_FIELDS:
                    if f in vals:
                        setattr(r, f, int(vals[f]))
                recs[int(k)] = r
            p.batting_records = recs
        if 'bowling_records' in data:
            recs = {}
            for k, vals in data['bowling_records'].items():
                if not 0 <= int(k) < len(self.pf.record_types):
                    raise ValueError('unknown record type %s' % k)
                r = p.bowling_records.get(int(k)) or BowlRecord(*[0] * 7)
                for f in BOWL_FIELDS:
                    if f in vals:
                        setattr(r, f, int(vals[f]))
                recs[int(k)] = r
            p.bowling_records = recs
        self.dirty = True
        return self.player_json(i)

    def add_player(self, data):
        template = self.pf.players[int(data.get('template', 0))]
        first, sur = str(data.get('first_name', '')).strip(), str(data.get('surname', '')).strip()
        if not sur:
            raise ValueError('surname is required')
        (first + sur).encode('latin-1')
        p = self.pf.add_player(template, first, sur, str(data.get('initials', '')).strip(),
                               int(data.get('team', 0)) or None)
        self.dirty = True
        return self.player_json(self.pf.players.index(p))

    def delete_player(self, i):
        p = self.pf.players[i]
        self.pf.remove_player(p.key)
        self.dirty = True
        return {'deleted': p.name}

    # -------------------------------------------------------------- teams
    def crteam(self, team_id):
        return next((t for t in self.pf.teams if t.name_ref == team_id), None)

    def team_detail(self, i):
        t = self.tf.teams[i]
        d = dict(dataclasses.asdict(t), index=i, club=None)
        ct = self.crteam(t.id)
        if ct is None:
            return d
        by = {p.key: (j, p) for j, p in enumerate(self.pf.players)}
        def row(k):
            j, p = by.get(k, (None, None))
            if p is None:
                return {'key': k, 'index': None, 'name': '(missing player %d)' % k}
            return {'key': k, 'index': j, 'name': p.name, 'wicketkeeper': p['wicketkeeper'],
                    'batting': round(self.pf.batting(p)), 'bowling': round(self.pf.bowling(p))}
        d['club'] = {
            'national': bool(ct.national), 'minor': bool(ct.minor),
            'squad': [row(k) for k in ct.squad], 'selected': [row(k) for k in ct.selected],
            'roles': {f: (None if getattr(ct, f) == 0xFFFF else getattr(ct, f)) for f in ct.INDEX_FIELDS},
            'money': {f: getattr(ct, f) for f in TEAM_MONEY},
            'year_founded': ct.year_founded, 'colour': ct.colour, 'history': ct.history,
        }
        return d

    def update_team_detail(self, i, data):
        self.update_team(i, data)
        t = self.tf.teams[i]
        ct = self.crteam(t.id)
        club = data.get('club')
        if club and ct is not None:
            if 'squad' in club:
                keys = [int(k) for k in club['squad']]
                known = {p.key for p in self.pf.players}
                if any(k not in known for k in keys):
                    raise ValueError('unknown player in squad')
                if ct.national:
                    ct.squad = keys
                else:
                    for k in set(ct.squad) - set(keys):
                        self.pf.move_player(k, 0)
                    for k in keys:
                        if k not in ct.squad:
                            self.pf.move_player(k, t.id)
                    ct.squad = [k for k in keys if k in ct.squad] + [k for k in ct.squad if k not in keys]
            if 'selected' in club:
                sel = [int(k) for k in club['selected']]
                if len(sel) != len(set(sel)):
                    raise ValueError('a player is picked twice')
                if any(k not in ct.squad for k in sel):
                    raise ValueError('picked players must be in the squad')
                ct.selected = sel
            for f, v in club.get('roles', {}).items():
                if f in ct.INDEX_FIELDS:
                    v = 0xFFFF if v is None or v == '' else int(v)
                    if v != 0xFFFF and not 0 <= v < len(ct.selected):
                        raise ValueError('%s must point at a picked player' % f)
                    setattr(ct, f, v)
            for f, v in club.get('money', {}).items():
                if f in TEAM_MONEY:
                    setattr(ct, f, max(0, int(v)))
            if 'year_founded' in club:
                ct.year_founded = int(club['year_founded'])
            if 'colour' in club:
                ct.colour = int(club['colour']) & 0xFF
            for f in ct.INDEX_FIELDS:   # keep roles valid if the XI shrank
                if getattr(ct, f) != 0xFFFF and getattr(ct, f) >= len(ct.selected):
                    setattr(ct, f, 0xFFFF)
        self.dirty = True
        return self.team_detail(i)

    def teams_json(self):
        counts = {}
        for p in self.pf.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        clubs = {t.name_ref: t for t in self.pf.teams}
        return [dict(dataclasses.asdict(t), index=i, players=counts.get(t.id, 0),
                     squad=len(clubs[t.id].squad) if t.id in clubs else None,
                     national=bool(clubs[t.id].national) if t.id in clubs else False)
                for i, t in enumerate(self.tf.teams)]

    def update_team(self, i, data):
        t = self.tf.teams[i]
        for k in ('name', 'short_name', 'abbrev'):
            if k in data:
                str(data[k]).encode('latin-1')
                setattr(t, k, str(data[k]))
        self.dirty = True
        return dataclasses.asdict(t)

    def update_ground(self, i, data):
        g = self.tf.grounds[i]
        for k in ('name', 'full_name', 'address', 'town', 'description'):
            if k in data:
                str(data[k]).encode('latin-1')
                setattr(g, k, str(data[k]))
        self.dirty = True
        return dataclasses.asdict(g)

    def update_records(self, i, data):
        rec = self.tf.records[i]
        cur = dataclasses.asdict(rec)
        cur.pop('lead'); cur.pop('unknown')
        _merge(cur, data)
        rec.partnerships = [teamfile.Partnership(**x) for x in cur['partnerships']]
        rec.highest_total = teamfile.TeamTotal(**cur['highest_total'])
        rec.lowest_total = teamfile.TeamTotal(**cur['lowest_total'])
        rec.most_runs_season = teamfile.SeasonBest(**cur['most_runs_season'])
        rec.most_wickets_season = teamfile.SeasonBest(**cur['most_wickets_season'])
        rec.best_bowling_match = teamfile.BestBowling(**cur['best_bowling_match'])
        rec.best_bowling_innings = teamfile.BestBowling(**cur['best_bowling_innings'])
        rec.highest_score = teamfile.HighScore(**cur['highest_score'])
        teamfile.serialize(self.tf)   # validate that everything still encodes
        self.dirty = True
        return records_json(rec, i)

    # --------------------------------------------------------------- save
    def save(self):
        # Build and fully verify both encrypted files in memory before touching the disk,
        # so a bug can never leave a corrupt (or half-saved) pair behind.
        pdata = playerfile.serialize(self.pf)
        tdata = teamfile.serialize(self.tf)
        fx_out = self.fx.build() if self.fx else []
        out = []
        for path, data, seed, mod in ((self.players_path, pdata, self.pf.fmt.seed_with_length, playerfile),
                                      (self.teams_path, tdata, self.tf.seed_with_length, teamfile)):
            blob = struct.pack('<I', len(data)) + encode(data, seed)
            if decode(blob[4:], seed) != data:
                raise SaveError('encryption check failed for %s' % path.name)
            reparsed = playerfile.parse(data, self.pf.fmt) if mod is playerfile else teamfile.parse(data)
            if mod.serialize(reparsed) != data:
                raise SaveError('re-read check failed for %s' % path.name)
            out.append((path, blob))

        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        fx_saved, fx_backups = self.fx.write(fx_out, stamp) if fx_out else ([], [])
        backups = list(fx_backups)
        for path, _ in out:
            if path.exists():
                b = path.with_name(path.name + '.bak-' + stamp)
                shutil.copy2(path, b)
                backups.append(str(b))
        for path, blob in out:
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_bytes(blob)
            os.replace(tmp, path)
        # Final check of what actually landed on disk.
        if playerfile.serialize(playerfile.load(self.players_path)) != pdata or \
                teamfile.serialize(teamfile.load(self.teams_path)) != tdata:
            raise SaveError('files on disk do not match after saving; restore from %s' % ', '.join(backups))
        self.dirty = False
        return {'saved': [str(self.players_path), str(self.teams_path)] + fx_saved, 'backups': backups}

    # ----------------------------------------------------------- fixtures
    def fixtures(self):
        if not self.fx:
            raise KeyError('no fixture files loaded')
        return self.fx

    def _venue_info(self):
        gname = {g.id: g.name for g in self.tf.grounds}
        grounds = {t.name_ref: [gname.get(x, '#%d' % x) for x in t.grounds] for t in self.pf.teams}
        return {'team_grounds': grounds, 'tests': {t.name_ref for t in self.pf.teams if t.national}, 'grounds': gname}

    def fixture_files(self):
        return self.fixtures().list()

    def fixture_file(self, name):
        self.fixtures().venue_info = self._venue_info()
        return self.fixtures().file_json(name, self.team_names())

    def update_fixture(self, name, i, data):
        self.fixtures().venue_info = self._venue_info()
        r = self.fixtures().update(name, i, data, self.team_names())
        self.dirty = True
        return r

    def add_fixture(self, name, data):
        self.fixtures().venue_info = self._venue_info()
        r = self.fixtures().add(name, data, self.team_names())
        self.dirty = True
        return r

    def delete_fixture(self, name, i):
        r = self.fixtures().delete(name, i)
        self.dirty = True
        return r

    def update_fixture_keys(self, name, data):
        r = self.fixtures().update_keys(name, data)
        self.dirty = True
        return r


def records_json(rec, i):
    d = dataclasses.asdict(rec)
    d.pop('lead'); d.pop('unknown')
    d['index'] = i
    return d


def _merge(dst, src):
    for k, v in src.items():
        if k not in dst:
            continue
        if isinstance(dst[k], dict) and isinstance(v, dict):
            _merge(dst[k], v)
        elif isinstance(dst[k], list) and isinstance(v, list):
            for a, b in zip(dst[k], v):
                _merge(a, b)
        else:
            dst[k] = type(dst[k])(v) if dst[k] is not None else v


def meta_record_owners():
    return ['Derbyshire', 'Durham', 'Essex', 'Glamorgan', 'Gloucestershire', 'Hampshire',
            'Kent', 'Lancashire', 'Leicestershire', 'Middlesex', 'Northamptonshire',
            'Nottinghamshire', 'Somerset', 'Surrey', 'Sussex', 'Warwickshire',
            'Worcestershire', 'Yorkshire', 'Australia', 'England', 'India', 'New Zealand',
            'Pakistan', 'South Africa', 'Sri Lanka', 'West Indies', 'Zimbabwe', 'Bangladesh']


def editable_fields(pf):
    """Bitfields the editor exposes. Nationality has its own control; ICC 2006 keeps
    batting/bowling as decimals, so their old packed bits are left untouched."""
    skip = {'national_team'}
    if pf.fmt.float_abilities:
        skip |= {'batting', 'bowling'}
    return [f for f in BITFIELDS if f.name not in skip]


def meta(store):
    return {
        'players_path': str(store.players_path), 'teams_path': str(store.teams_path),
        'dirty': store.dirty,
        'duplicate_keys': store.pf.duplicate_keys(),
        'record_types': store.pf.record_types,
        'float_abilities': store.pf.fmt.float_abilities,
        'game_version': store.pf.game_version,
        'national_teams': {str(k): (store.team_names().get(v) if v else 'None')
                           for k, v in store.pf.national_teams.items()},
        'bowler_types': playerfile.BOWLER_TYPES, 'bat_types': playerfile.BAT_TYPES,
        'fields': [dict(name=f.name, label=f.label, group=f.group, max=f.max_raw, lo=f.lo, hi=f.hi)
                   for f in editable_fields(store.pf)],
        'kind': 'classic',
        'career_records': True,
        'fixtures_dir': str(store.fx.folder) if store.fx else None,
    }


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    folder = None          # game folder of the open store (app mode)
    app_mode = False       # folder chooser enabled; no store needed at start
    open_lock = threading.Lock()
    last_ping = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def _route(self, method):
        s = self.store
        parts = [x for x in self.path.split('?')[0].split('/') if x]
        if method == 'GET' and not parts:
            return self._send(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if method == 'GET' and len(parts) == 1 and parts[0] in STATIC_FILES:
            return self._send(200, (STATIC / parts[0]).read_bytes(), STATIC_FILES[parts[0]])
        if not parts or parts[0] != 'api':
            return self._send(404, {'error': 'not found'})
        parts = parts[1:]
        if parts and parts[0] in ('ping', 'app', 'open', 'browse', 'quit') or s is None:
            return self._app_route(method, parts)
        if method == 'GET' and parts == ['meta']:
            with s.lock:
                return self._send(200, dict(s.meta(), folder=self.folder, app=self.app_mode, version=__version__))
        with s.lock:
            try:
                if method == 'GET':
                    if parts == ['meta']:
                        return self._send(200, s.meta())
                    if parts == ['players']:
                        return self._send(200, s.players_list())
                    if len(parts) == 2 and parts[0] == 'players':
                        return self._send(200, s.player_json(int(parts[1])))
                    if parts == ['teams']:
                        return self._send(200, s.teams_json())
                    if len(parts) == 2 and parts[0] == 'teams':
                        return self._send(200, s.team_detail(int(parts[1])))
                    if parts == ['grounds']:
                        return self._send(200, s.grounds_list())
                    if parts == ['records']:
                        return self._send(200, s.records_list())
                    if parts == ['fixtures']:
                        return self._send(200, s.fixture_files())
                    if len(parts) == 2 and parts[0] == 'fixtures':
                        return self._send(200, s.fixture_file(unquote(parts[1])))
                elif method == 'PUT' and len(parts) == 3 and parts[0] == 'fixtures':
                    if parts[2] == 'keys':
                        return self._send(200, s.update_fixture_keys(unquote(parts[1]), self._body()))
                    return self._send(200, s.update_fixture(unquote(parts[1]), int(parts[2]), self._body()))
                elif method == 'PUT' and len(parts) == 2:
                    kind, i = parts[0], int(parts[1])
                    data = self._body()
                    if kind == 'players':
                        return self._send(200, s.update_player(i, data))
                    if kind == 'teams':
                        return self._send(200, s.update_team_detail(i, data))
                    if kind == 'grounds':
                        return self._send(200, s.update_ground(i, data))
                    if kind == 'records':
                        return self._send(200, s.update_records(i, data))
                elif method == 'POST' and parts == ['save']:
                    return self._send(200, s.save())
                elif method == 'POST' and parts == ['players']:
                    return self._send(200, s.add_player(self._body()))
                elif method == 'POST' and len(parts) == 2 and parts[0] == 'fixtures':
                    return self._send(200, s.add_fixture(unquote(parts[1]), self._body()))
                elif method == 'DELETE' and len(parts) == 3 and parts[0] == 'fixtures':
                    return self._send(200, s.delete_fixture(unquote(parts[1]), int(parts[2])))
                elif method == 'DELETE' and len(parts) == 2 and parts[0] == 'players':
                    return self._send(200, s.delete_player(int(parts[1])))
            except (ValueError, KeyError, IndexError, TypeError, UnicodeEncodeError) as e:
                return self._send(400, {'error': str(e)})
            except SaveError as e:
                return self._send(500, {'error': 'Not saved: %s' % e})
            except Exception as e:  # never leave the browser hanging on an unexpected bug
                return self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})
        return self._send(404, {'error': 'not found'})

    def _app_route(self, method, parts):
        cls = type(self)
        if parts == ['ping']:
            cls.last_ping = time.monotonic()
            return self._send(200, {'ok': True})
        if method == 'POST' and parts == ['quit']:   # the page's Quit button (it has already warned about unsaved changes)
            self._send(200, {'quitting': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        if method == 'GET' and parts in (['app'], ['meta']):
            return self._send(200, {'needs_folder': self.store is None, 'app': self.app_mode, 'version': __version__,
                                    'folder': self.folder, 'recent': app.recent_folders()})
        if method == 'GET' and parts == ['browse']:
            if not self.app_mode:
                return self._send(403, {'error': 'not available'})
            query = parse_qs(urlparse(self.path).query)
            try:
                return self._send(200, app.browse((query.get('path') or [''])[0]))
            except ValueError as e:
                return self._send(400, {'error': str(e)})
        if method == 'POST' and parts == ['open']:
            if not self.app_mode:
                return self._send(403, {'error': 'not available'})
            path = str(self._body().get('path') or '')
            try:
                with cls.open_lock:
                    store = open_game(path)
                    cls.store, cls.folder = store, str(Path(path).resolve())
                    app.remember_folder(path)
            except (ValueError, SystemExit, OSError) as e:
                return self._send(400, {'error': 'Could not open %s: %s' % (path, e)})
            except Exception as e:
                return self._send(500, {'error': 'Could not open %s: %s: %s' % (path, type(e).__name__, e)})
            return self._send(200, {'opened': cls.folder, 'game': store.game_label})
        return self._send(409, {'error': 'no game folder is open'})

    def do_GET(self):
        self._route('GET')

    def do_PUT(self):
        self._route('PUT')

    def do_POST(self):
        self._route('POST')

    def do_DELETE(self):
        self._route('DELETE')


class GameNotFound(ValueError):
    pass


def find_game_files(game_dir):
    """Locate the databases and fixtures inside a game install (or any folder holding them).

    ICC 2000/2002 keep dataT.db / DataP.db and the .fxt files in the game folder; ICC 2006 keeps the
    databases in Data\\ and the fixtures in Fxt\\; ICC 2 has a single database.db.
    """
    root = Path(game_dir)
    if not root.is_dir():
        raise GameNotFound('not a folder: %s' % root)
    for d in (root, root / 'Data'):
        players = sorted(d.glob('[dD][aA][tT][aA][tT].[dD][bB]'))
        teams = sorted(d.glob('[dD][aA][tT][aA][pP].[dD][bB]'))
        if players and teams:
            return {'players': players[0], 'teams': teams[0], 'fixtures': find_fixture_dir(root, d)}
    for d in (root, root / 'Data'):
        db = d / 'database.db'
        if db.is_file():
            from .. import icc2file
            try:
                icc2file.load(db)
            except ValueError:
                continue
            return {'database': db}
    raise GameNotFound('no ICC database found in %s (looked for dataT.db + DataP.db, or ICC 2 database.db)' % root)


def open_game(game_dir, game=None):
    """Load the store for a game folder (used by the app's folder chooser and --game-dir)."""
    found = find_game_files(game_dir)
    if found.get('database'):
        from .icc2store import ICC2Store
        return ICC2Store(found['database'])
    return Store(found['players'], found['teams'], game, found['fixtures'])


def main(argv=None):
    ap = argparse.ArgumentParser(description='ICC database editor')
    ap.add_argument('--players', help='player file (dataT.db); default Original DB/2002/dataT.db')
    ap.add_argument('--teams', help='team file (dataP.db); default Original DB/2002/dataP.db')
    ap.add_argument('--game', type=int, choices=sorted(playerfile.NATIONAL_TABLES) + [2006],
                    help='game release (default: detected from the database)')
    ap.add_argument('--database', help='ICC 2 (1999) database.db - edits that single file instead')
    ap.add_argument('--game-dir', help='game install folder: finds the databases and fixture files in it')
    ap.add_argument('--fixtures', help='folder of .fxt fixture files (default: next to the player file, or its Fxt/)')
    ap.add_argument('--port', type=int, default=8002)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--app', action='store_true',
                    help='desktop app: own window (Edge/Chrome app mode), choose game folders in the app, '
                         'quit when the window closes')
    ap.add_argument('--embedded', action='store_true',
                    help='run inside the macOS app wrapper: print the URL, quit when stdin closes')
    args = ap.parse_args(argv)
    if sys.stdout is None:   # pythonw.exe on Windows has no console
        log = app.config_dir() / 'editor.log'
        sys.stdout = sys.stderr = open(log, 'a', buffering=1)
    app_mode = args.app or args.embedded
    try:
        _load_initial_store(args, app_mode)
    except (GameNotFound, SystemExit, OSError, ValueError) as e:
        if not app_mode:
            raise SystemExit(str(e))
        app.show_error('Could not open the game folder:\n%s' % e)
        Handler.store = Handler.folder = None
    Handler.app_mode = app_mode

    for port in range(args.port, args.port + 20):   # another editor may already be running
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit('no free port between %d and %d' % (args.port, args.port + 19))
    url = 'http://localhost:%d' % port
    store = Handler.store
    print('ICC editor on %s  (%s)' % (url, 'ICC %s, %s' % (store.game_label, Handler.folder or 'Original DB')
                                     if store else 'no game open yet'), flush=True)

    if args.embedded:
        print('ICC-EDITOR-URL %s' % url, flush=True)
        threading.Thread(target=_quit_when_stdin_closes, args=(httpd,), daemon=True).start()
    elif args.app:
        window = app.open_window(url)
        if window is not None:
            threading.Thread(target=_quit_when_closed, args=(httpd, window), daemon=True).start()
        else:   # no Chromium browser: ordinary tab; quit once the page stops pinging
            webbrowser.open(url)
            threading.Thread(target=_quit_when_idle, args=(httpd,), daemon=True).start()
    elif not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def _load_initial_store(args, app_mode):
    if args.game_dir:
        Handler.store = open_game(args.game_dir, args.game)
        Handler.folder = str(Path(args.game_dir).resolve())
        if app_mode:
            app.remember_folder(args.game_dir)
    elif args.database:
        from .icc2store import ICC2Store
        Handler.store = ICC2Store(args.database)
    elif args.players or args.teams or not app_mode:
        Handler.store = Store(args.players or 'Original DB/2002/dataT.db', args.teams or 'Original DB/2002/dataP.db',
                              args.game, args.fixtures)


def _quit_when_closed(httpd, window, idle=150):
    """Quit when the app window's browser process ends or - since Edge/Chrome may keep running in
    the background after the window closes - once the page has stopped pinging for a while.
    (Hidden windows still ping about once a minute.)"""
    while True:
        try:
            window.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            if Handler.last_ping and time.monotonic() - Handler.last_ping > idle:
                break
    httpd.shutdown()


def _quit_when_stdin_closes(httpd):
    try:
        while sys.stdin.read(1024):
            pass
    except (OSError, ValueError):
        pass
    httpd.shutdown()


def _quit_when_idle(httpd, idle=90):
    started = time.monotonic()
    while True:
        time.sleep(5)
        last = Handler.last_ping or started
        if time.monotonic() - last > idle:
            httpd.shutdown()
            return


if __name__ == '__main__':
    main()
