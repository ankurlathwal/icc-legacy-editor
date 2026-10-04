# Project Summary
Decode the database files of the old game International Cricket Captain (ICC 1998, ICC 2 (1999), 2000, 2001, 2002, 2006,
plus Australian Cricket Captain (1998)) and provide a visual editor that saves back into the game's own format, so edited databases can be played in the game.

# The database files
Each game release uses a pair of files:
- **`dataT.db` = PLAYER data** (players, plus the team-squad and coach sections).
- **`DataP.db` / `dataP.db` = TEAM data** (team names, grounds, club record books).

What is in the repo:
- `Original DB/` — the working databases (edit these; `.bak-*` backups are git-ignored):
  - `Original DB/2002/` — clean ICC 2002 release pair (the editor's CLI default) + its `.fxt` files.
  - `Original DB/2000/` — ICC 2000 pair, edited by the user (Sehwag, Yuvraj Singh added; roles/names fixed) and verified in-game, + `.fxt`.
  - `Original DB/2006/` — ICC 2006 pair (edits verified in-game) + `Fxt/` (one fixture file per season 2005–2025).
  - `Original DB/1999/database.db` — ICC 2 (1999), a single unencrypted file (edits verified in-game). ICC 2 has no fixture files.
- The game installs are NOT in the repo (copyright/size). On the user's Windows PC they are under `C:\ICC\` (`1998`, `1999`, `2000`,
  `2001`, `2002`, `2006`, `Australian Cricket Captain`). `C:\ICC\2002` is the Mac copy: its `DataP.db` is the
  text-editor-damaged one (decrypted payload starts with `EF BF BD`) and its `dataT.db` the "2005 update"; use `Original DB/2002/`. Their DLLs are the reference for the file format:
  `CrManAndEng.dll`, `CrTypes.dll`, `CrickMan.dll`, `Global.dll` (ICC 2: `CrickMan.dll` + `CrTypes.dll`). Paths written below
  as `ICC 2000/...`, `ICC 2006/...`, `ICC2/...` mean "inside that game's install folder".

Known-bad / special files (seen in the user's ICC 2002 install on the Mac; check before trusting an install's files):
- That install's `DataP.db` was **corrupted** (edited in a text editor: "Durham"→"Dirkham", every byte ≥ 0x80 replaced with
  UTF-8 `EF BF BD`); its `dataT.db` was the unofficial "2005 update", which reused other players' slots (e.g. Andy Flower in
  Justin Bishop's slot with Bishop's stats) and has a duplicate map key (1647). Treat such oddities as data issues, not parser
  bugs; `Original DB/2002/` is the clean pair. `ICC 2002/database.db` is not a player/team file.
- ICC 2000 copies may be a compressed "DiVINE" release: `bkdps`, `grnds`, `maps`, `mlms`, `photos` packed as `.ARH`
  archives that `unpack.bat` extracts with `ARHANGEL.EXE`, an MS-DOS program 64-bit Windows cannot run (DOSBox can).
  Without them the game has no backdrops and screens draw over each other. The user's install was fixed (2026-10-01) by
  copying in folders extracted with DOSBox.
- ICC 2006 keeps its databases in `Data\` and fixtures in `Fxt\`.

# Format (all reverse-engineered from the game DLLs)
- Encryption (`Global.dll` `CrEncryptedSerialize`): 4-byte LE length, then payload. Encode = pseudo-random block swaps (MSVC `rand()` seeded with the byte sum) then `+ (i*0xB3)` per byte. Implemented in `icc/crypto.py`; identical for 2000 and 2002.
- Payloads are MFC `CArchive` streams (CString, object tags `0xFFFF`/`0x8000|index`).
- Class layouts come from `CrManAndEng.dll` (`CrPerson`, `CrPlayer`, `CrBowler`, `CrTeam`, `CrCoach`, `CrDatabase::SerializeTemporary`) and `CrTypes.dll` (career records). Ability fields are bit-packed and scaled by `CrPerson::uintToAbility` (`min + raw*(max-min)/(2^bits-1)`).
- 2000 vs 2002: identical layout and bitfields; only the nationality code table differs (2002 adds Namibia=1, Canada=14, Netherlands=15). `PlayerFile.game_version` auto-detects.
- ICC 2006 (MFC71 build) — `playerfile.FORMAT_2006`, detected by `playerfile.load`:
  - encryption seeds `rand()` with byte sum **+ length** (team and player files);
  - `CrPerson` adds a DWORD national team id after the wages (4-bit nationality code no longer used);
  - 24 career-record types (T20 added, Net records moved to 22/23); mask = BYTE + two WORDs (`Format.mask_words`);
  - `CrPlayer` adds DWORD England-contracted + batting as a double (x4096 = CrFixed); `CrBowler` adds bowling as a double; the old 9-bit batting/bowling bits are kept raw but unused;
  - `CrTestHistory` is 160 bytes (10 Test nations). Team file (`dataP`) layout is unchanged.
- ICC 2 (1999) — `icc/icc2file.py`, from `ICC2/CrickMan.dll` + `CrTypes.dll` (MFC42, no `Global.dll`, no encryption):
  - one `database.db`: CrTeamNames, then CrDbase of CrBowler (players), CrCoach, CrGround, CrTeam; each CrDbase = CMapWordToOb + sorted WORD key array + 2 WORDs (player DB adds 3 WORD arrays);
  - abilities are unpacked 32-bit floats (names from getters, see `icc2file.FIELDS`); fixed 16/16/15 bat/bowl/field record blocks, each in its own slot order (`BAT_ORDER`/`BOWL_ORDER`/`FIELD_ORDER` → `RECORD_TYPES`), accessed via `get_record`/`set_record`; coaches are full CrPerson;
  - CrTeam holds squad/XI WORD lists, string-list history, two CrTeamRecords books (first-class, one-day), 84 fixed bytes (named in `icc2file.TEAM_FIXED`: roles, income, budgets, national flag...) and a 576-byte history block.
  - Editor: `python3 -m icc.editor --database ...` uses `icc/editor/icc2store.py` (same JSON API as `server.Store`).
- ICC 2001: identical files to ICC 2000 (same layout and nationality table, checked in its `CrManAndEng.dll`), so it is
  told apart only by `fix1.fxt`'s season year (2001); `server.Store` then sets `game_version = 2001`
  (`NATIONAL_TABLES[2001]` = the 2000 table).
- ICC 1998 (original International Cricket Captain) and Australian Cricket Captain (1998), both the ICC 1 engine —
  `icc/accfile.py` (`accfile.ICC1`, `accfile.ACC` Format objects; `accfile.parse` detects which), from their
  `CrickMan.dll` + `CrTypes.dll` (MFC42). ICC 1998 differs from ACC only in: 10 bat/bowl + 9 field records (no
  'First-class only', no Second XI), Championship/Sunday League history as DWORD arrays, colour stored after the first
  8 team-value bytes, no default team size (60 value bytes) and a 216-byte history block (18 x 3 DWORDs). ACC details:
  - same outline as ICC 2 (`database.db`: CrTeamNames + CrDbase of CrBowler / CrCoach / CrGround / CrTeam, unencrypted);
  - CrPerson: as ICC 2 up to nationality, then morale + loyalty (doubles), contract length, 3 wage floats; no notes string;
  - abilities are 64-bit doubles at fixed byte offsets of `ptail` (276 bytes) / `btail` (196) (`accfile.FIELDS`);
  - career records: 13 bat + 13 bowl (8 DWORDs each) + 11 field; best bowling is runs then wickets (Warne 8/71) even though
    the DLL setter names say the opposite;
  - CrTeam: ground/squad/XI lists, CrCountry (28 bytes), 2 more player-key lists, history, colour, 64 fixed bytes
    (`accfile.TEAM_FIXED`), 576-byte history; no club record books; grounds have 24 bytes of conditions;
  - editor: `icc2store.ACCStore` (subclass of `ICC2Store`, `F` = the detected Format; `open_database` picks ICC 2 or ICC 1);
  - fixtures `eng98.fxt` (1998) / `aus1998.fxt` / `ausX.fxt` (ACC): `icc/fixture1file.py`, see below;
  - the game refuses to start on Windows 10 with "requires ... 'Small Fonts'": `cricket.exe` reads
    `HKEY_CURRENT_CONFIG\Display\Settings` `DPILogicalX` (a Win9x value) and wants "96"; creating it (admin) fixes it
    (user confirmed). In the Help guide's troubleshooting.
- Fixtures (`*.fxt`, ICC 2000/2002/2006) — `icc/fixturefile.py`, from `CrEventList::serializeFixtures`, `CrDay`, `CrFixture`,
  `CrResult` (CrickMan.dll) and `CrMatchType` (CrTypes.dll). Plain (unencrypted) CArchive:
  - 365 CrDay (WORD day, DWORD count, CrFixture objects; a multi-day match is written once and referenced on its later days,
    span = `getNoDays` of its match type). Day 0 = 31 March of the season year (day 270 = 26 Dec).
  - CrFixture: CrMatchType (5 bytes: type nibble, round, out_of, day/night, tri-series no.), CrResult (kept raw, unplayed),
    WORD home, WORD away (fixture-key slots), WORD ground, WORD start day, BYTE flag, null match-data object.
  - ground: ICC 2000/2002 = a team id (play at that team's ground); ICC 2006 = a ground id; 0 = home side's ground
    (Test nations use their Nth ground for match N; domestic finals go to England's 2nd ground, Lord's).
  - Tail: fixture key (300 WORDs; 305 in 2006 except its 2005 files) mapping slot → team id (0xCDCD = unused; slots 1–18
    Championship div 1/2, 101–128 international rotation, 221–238 National League), 18 DWORDs, 28×12 rotation table,
    5×5 tri-series table, then 8 bytes (2000/2002: year + 1 DWORD) or 30 bytes (2006: 6 DWORDs + 3 WORDs).
  - Team placeholders: slot 0 = to be decided, 850+ = World Cup Super Six place, 900+ = cup draw position.
  - A fixture whose venue resolves to no ground crashes the game at match start (user's Kenya v India ODIs in ICC 2006:
    Kenya has no grounds in the player file). The editor rejects such fixtures and flags them in the list.
  - `CrTeam::isTestTeam` hard-codes Test nations as team ids 43–51 (2000/2002) / 43–52 (2006). Tests, ODIs, tri-series and
    (2006) Int T20 with any other side fail at match start with MFC's "An invalid argument was encountered" (seen in-game
    with Kenya v India, ICC 2006). Associates appear only in World Cup fixtures (2000/2002 wc.fxt). The editor blocks
    adding such internationals (`fixtures.check_international`); `mp.fxt` has odd existing entries, so only changes are checked.
  - Order inside each day is the game's compile order; `fixturefile` recovers one global order (topological sort) so files
    round-trip exactly; `serialize` regenerates the days from each fixture's start + length.
- Fixtures of ICC 1998 / ACC (ICC 1 engine) — `icc/fixture1file.py`, from `CrEventList::Serialize`, `CrDay`, `CrFixture`
  (CrickMan.dll) and `CrMatchType` (CrTypes.dll); plain CArchive, a different layout from `fixturefile`:
  - 365 CrDay = DWORD count + 30 WriteObject slots (null tags pad). A match is written in full on its first day; each later
    day gets its own small CrFixture (all -1 / 0xCDCD, first=0, last-day flag, day-in-match) referencing the first-day one.
  - CrFixture: CrMatchType (DWORD type, round, out_of; ACC + DWORD day/night + CString = ODI Tournament number), DWORD home,
    away (ACC + WORD ground = team id), start, first, last (1 for one-day matches, else 0xCDCD), day-in-match, 3 objects.
  - Tail: 9 CrCountry (28 bytes), 150 team-name CStrings, 4x6 DWORDs, rotation table (12 columns, column 0 = this file's
    season; 1998 22 rows, ACC 28), ACC 5x5 DWORDs, then 1998 5+4+5 / ACC 4 DWORDs (year = 1998 tables[-4], ACC tables[-3]).
  - Teams: slot < 101 = position in the name table (1998: id = pos + 1; ACC table starts empty, pos = id); 101+ = rotation
    slot (entries are name-table positions); ACC 0 / 900+ and 1998 unnamed positions = knockout placeholders.
  - Match types: 1998 file codes 0 Test, 1 ODI, 2 Championship, 3 Sunday League, 4 "League Cup" (B&H), 5 NatWest, translated to
    the editor's ICC 2000 codes (`TYPES_1998`); ACC's codes already match (0 Sheffield Shield, 1 Mercantile Mutual Cup,
    4 World Cup, 5 ODI Tournament, 6 Test, 7 ODI, 8/9 friendlies, 10 Club Cricket). Day 1 = 1 April (1998) / 1 October (ACC),
    checked against real 1998 / 1998-99 dates.
  - Editor: `fixtures.load_fixture_file` falls back to `fixture1file`; same FixtureFile interface plus `slot_entries`,
    `set_slot` (rotation only) and `venue_editable` / `has_day_night` / `has_tt`. The "home side needs a ground" check is
    skipped for these games (their national sides have no grounds; Test venue rules not decoded).
- ICC 2 (1999) has no fixture files: its fixtures are text resources (type "FIXTURES", ids 0x93/0x94...) inside `Cricket2.exe`,
  read via `AMainWin::convertResourceToFile` + `CrEventList::readInText`. Editing them would mean patching the exe (not done).
  - The game's original *text* fixture sources (with format comments) are embedded in `ICC 2000/CrickMan.dll` around
    file offsets 0x4da00–0x635e5 (in the install) — useful reference for slot meanings.

# How formats were worked out (use the same method for anything new)
- Tools live in `tools/re/` (see its README; needs a venv with `capstone` + `pefile`, kept outside the repo), run against
  the DLLs in the game installs.
- Read the class's `Serialize` store path for the field order and sizes (`writes.py`, `dismod.py`), then name each
  field from the exported getters/setters that read the same member offset (`getters.py`).
- Bit-packed fields (2000/2002/2006): trace the *load* path with `symtrace.py` to map file bits → member bits;
  ranges come from the CrFixed constants pushed before `uintToAbility`.
- Always confirm against real data (famous players, real club records) and finish with a byte-exact round trip of
  every file (`python tools/roundtrip_check.py`, plus the game install folders as arguments). `ICC 2002/decrypted_db.db`
  (in that install, = decrypted ICC 2000 dataT) was a known-good decrypt sample.
- Value labels for bowler type / bat type were inferred from well-known players (game labels are in compressed
  `.acl` resources that were never decoded).

# Code
- `icc/crypto.py` — encode/decode, `read_db` / `write_db`.
- `icc/convdb.py` — drop-in replacement for the game's `convdb.exe` (`python3 -m icc.convdb -d|-e in out`).
- `icc/archive.py` — MFC CArchive reader/writer (regenerates object tags on save).
- `icc/teamfile.py` — team file: teams, grounds, club records.
- `icc/playerfile.py` — player file: players (with `BITFIELDS` table), career records, teams/squads/XI, coaches; add/remove/move players keeping squads consistent.
- `icc/icc2file.py` — ICC 2 (1999) single-file database.
- `icc/accfile.py` — ICC 1998 / Australian Cricket Captain single-file database (reuses `icc2file` building blocks).
- `icc/fixturefile.py` — fixture files (`*.fxt`, ICC 2000-2006); `icc/fixture1file.py` — ICC 1998 / ACC fixture files;
  `icc/editor/fixtures.py` — fixture editing + venue resolution for the editor (`FixtureStoreMixin` for both store kinds).
- `icc/editor/` — local web editor (stdlib `http.server` + single-page `static/index.html`); `server.Store` for 2000/2002/2006, `icc2store.ICC2Store` for ICC 2 (`ACCStore` for ICC 1998 / ACC).
- Desktop packages (the user runs the editor as an app on Windows against the game installs, and on the Mac):
  - `tools/build_windows.py` → `dist/ICC-Editor-Windows.zip`: embeddable Python 3.13 (cached in `dist/.cache`) + `icc/` +
    `ICC Editor.exe` (C launcher `tools/winlauncher/launcher.c` cross-compiled with Zig — GUI subsystem via
    `-Wl,--subsystem,windows`, icon/version/manifest embedded; runs `runtime\pythonw.exe -m icc.editor --app`) +
    `runtime\Troubleshoot.bat` (same with a console). Build needs Zig (`winget install zig.zig` / `brew install zig`);
    uses the committed `icc/editor/static/icon.ico`.
  - `tools/build_macos.py` (macOS only) → `dist/ICC Editor.app` + `dist/ICC-Editor-macOS.zip`: native WebKit wrapper
    (`tools/macapp/main.swift`, universal, macOS 13+, ad-hoc signed) running `/usr/bin/python3 -m icc.editor --embedded`
    from Contents/Resources/runtime; native NSOpenPanel via `window.webkit.messageHandlers.pickFolder`.
  - Icon: `tools/macapp/icon.swift` (macOS) draws `icc/editor/static/icon.png` (cricket ball on grass); `icon.ico` is
    generated from it (`build_windows.make_ico()`, uses macOS `sips`) and committed; the page uses the PNG as favicon/logo.
  - In-app guide: `icc/editor/static/help.html` (Help button; keep it in step with features and known game limits).
    Version: `icc/editor/__init__.py` `__version__` (shown in the guide, the Mac Info.plist and `/api/meta`).
  - Rebuild both after any editor change.
- `tools/roundtrip_check.py [folders...]` — byte-exact round trip of every database and `.fxt` file under the repo (or the
  given folders, e.g. the game installs); unparsable files are reported as SKIP. Run after any parser change.
- `tools/re/` — reverse-engineering helpers (disassembly, getter mapping, symbolic bit tracer).

# Editor architecture
- Run: `python -m icc.editor` (defaults to `Original DB/2002/`), `--players/--teams` for a pair, `--database` for ICC 2,
  `--game-dir DIR` (finds dataT/DataP or ICC 2 database.db in DIR or DIR/Data, fixtures in DIR or DIR/Fxt),
  `--app` (desktop app: chooser page `/api/app|open|browse` — an in-app folder browser marking ICC game folders, because a
  native dialog spawned by the background process opens behind the window on Windows; Edge/Chrome/Vivaldi `--app` window with its own profile in the
  config dir, quits when that process ends or pings stop; `icc/editor/app.py`), `--embedded` (Mac wrapper: prints
  `ICC-EDITOR-URL`, quits on stdin EOF), `--game` to override detection, `--fixtures DIR` (default: `*.fxt` next to the
  player file or in its `Fxt/`), `--port` (next free port is used if taken), `--no-browser`.
- Both stores expose the same JSON API (`/api/meta`, `players`, `players/<i>`, `teams`, `teams/<i>`, `grounds`, `records`,
  `save`, POST/DELETE players). `meta` tells the page what the version supports (`kind`, `fields` with `kind`/`max`/`lo`/`hi`,
  `record_types`, `career_records`, `float_abilities`, `national_teams`); the page adapts instead of branching on version.
- Save: build every file, decrypt + re-parse + compare **in memory** first, then write `.bak-<timestamp>` copies, write via
  temp file + `os.replace`, then re-read from disk. Never write before the in-memory check passes.
- Fixtures tab (only when `meta.fixtures_dir` is set): `/api/fixtures`, `fixtures/<file>` (GET/POST), `fixtures/<file>/<i>`
  (PUT/DELETE), `fixtures/<file>/keys` (PUT slot→team). Changed `.fxt` files are saved with the databases (same backups/checks).
- Squad consistency: domestic squads must equal the set of players whose team field points at the club; use
  `move_player`/`remove_player`/`add_player` (they also refill a vacated XI slot and fix captain/keeper/bowler indexes).
- Page UX the user asked for: list keeps search/filter/sort/scroll after Apply (`PLIST`, `refreshPlayers`); sticky Apply bar;
  player view split into Details / Stats tabs (`PTAB`); `beforeunload` warns about unapplied/unsaved changes.

# Still undecoded (kept raw, round-trips safely)
- 2000/2002/2006: CrForm (recent form), CrInternationalRating, 5-byte block + 2 lead bytes in each team-file record book,
  one WORD per CrBowlRecord (probably maidens).
- ACC: the 16-double / 32-DWORD tables at the start of ptail/btail, a few ptail/btail values, CrCountry, CrInjType, the
  8th DWORD of each bowling record, team lists 3/4 meaning, fixtures.
- ICC 2: one unknown DWORD per batting/bowling record entry, record-block trailer DWORDs, CrForm, CrInjType,
  Team.fixed `unknown_10`/`unknown_money`/`unknown_72`, ground pitch/weather blocks.
- Fixtures: the 18-DWORD county table, the extra tail DWORDs, CrFixture flag bit, rotation/tri-series tables (shown nowhere
  in the editor yet). ICC 1998 / ACC: CrCountry blocks, the 4x6 table, trailing values, how Tests pick a venue.
  Edited fixture files have NOT been tested in-game yet (any game).
- New player keys are max+1; proven fine in-game for ICC 2000.

# Conventions
- Use proper cricket terms in the UI: "List A" (not "One-day county"), "ODI", "First-class", "Second XI", "T20".
- Abilities: batting higher = better; bowling and economy lower = better (label them so).

# Working rules
- Every parser must round-trip byte-for-byte: after any format change, run `python tools/roundtrip_check.py` and also pass
  the game install folders.
- Test editor changes on copies in a scratch directory, never on `Original DB/` or the game folders.
- Stop a test editor server only by its port — never kill every python process or `pkill -f icc.editor`: that once stopped
  the user's own editor and lost unsaved edits. Windows: `Get-NetTCPConnection -LocalPort <port> -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess }`; Mac: `lsof -tiTCP:<port> -sTCP:LISTEN | xargs kill`.
  The editor also has `POST /api/quit`.
- After browser checks, close test tabs; clear `window.onbeforeunload` first or closing hangs on "Leave site?".
- Edited databases have been verified in the real game for ICC 2 (1999), ICC 2000 (including newly added players) and
  ICC 2006. ICC 2002 shares the 2000 layout but has not been tested in-game separately.
  ICC 1998, ICC 2001 and Australian Cricket Captain edits have NOT been tested in-game yet.
- The user runs the in-game tests (on Windows); never claim an in-game result that the user hasn't reported.
- Rebuild the Windows package (`python tools/build_windows.py`) after editor changes; the user shares it with the community.

# Working with the user
- Long-time ICC player/modder (once built an "ICC2002 IPL database"); owns ICC 2, 2000, 2002 and 2006. Knows cricket well —
  expects correct cricket terms and real-world player/team knowledge. Not into the reverse-engineering details: explain
  findings in plain cricket terms, keep the editor practical, and suggest a concrete in-game check after format changes.
- Plays on Windows 10 (ICC 2 runs full-screen with cnc-ddraw's default settings; ICC 2000 after unpacking its assets).
  Tried the games under Wine on the Mac, decided against it — don't suggest Wine.
- The editor is meant to be shared with the ICC community (everyone is on Windows): it runs as a desktop app (`ICC Editor.exe`
  → Edge app window, in-app folder browser, Help guide, Quit button), not a terminal + browser tab.
- UI preferences so far: "List A" naming; player list keeps its place after Apply; sticky Apply bar with clear feedback;
  Details / Stats tabs; filters (team, nationality); messages must not hide behind the Apply bar.

# Working on Windows
- Python 3.10+ for development (`py -3 -m icc.editor --game-dir "C:\path\to\game"`); the editor itself needs only the
  standard library. The Windows package bundles its own Python, so users install nothing.
- Build the user package: `py -3 tools/build_windows.py` (needs Zig: `winget install zig.zig`; downloads the embeddable
  Python once into `dist/.cache`). Output: `dist/ICC-Editor-Windows.zip` (`ICC Editor.exe` + `runtime/`).
- Mac-only pieces (keep, but they can't be built on Windows): `tools/build_macos.py`, `tools/macapp/*.swift` (Mac app and
  icon drawing), and regenerating `icon.ico` (uses `sips`).
- RE tools: `py -3 -m venv %TEMP%\icc-venv` then `%TEMP%\icc-venv\Scripts\pip install capstone pefile`.
