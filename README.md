# ICC Database Editor

An editor for the player and team databases of **International Cricket Captain (1998), ICC 2 (1999), 2000, 2001, 2002
and 2006**, and of **Australian Cricket Captain (1998)**. It reads the game's encrypted `.db` files directly and saves them back in the same format, so edited databases work in the game.

Needs Python 3.8+ only. Nothing to install.

## Desktop app (Windows and Mac)

The editor runs as its own app window; you pick the game folder inside the app, and it edits the
game's own files (keeping `.bak-<date-time>` copies on every save).

- **Windows:** unzip `dist/ICC-Editor-Windows.zip` anywhere and double-click **`ICC Editor.exe`** (keep it next to its
  `runtime` folder). It opens in a Microsoft Edge app window (no tabs, no console) and needs nothing installed.
  Closing the window quits it. If nothing appears, `runtime\Troubleshoot.bat` shows the error. On first run SmartScreen may
  warn about an unsigned app: More info → Run anyway.
- **Mac:** unzip `dist/ICC-Editor-macOS.zip` and open **`ICC Editor.app`**, a native window using the Mac's built-in
  WebKit (Safari's engine) and the Mac's own `python3`. Quit with ⌘Q or by closing the window; it warns about unsaved changes.

The **Help** button opens a full how-to guide inside the app. On the start screen, click **Choose game folder…** (or pick a recent one). The editor finds `database.db`
(ICC 2), `dataT.db` + `DataP.db` + `*.fxt` (ICC 2000 / 2002) or `Data\` + `Fxt\` (ICC 2006).
**Open another game** in the header switches games. Close the game itself before saving.

Rebuild the packages after changing the editor: `python3 tools/build_windows.py` (bundles the official
embeddable Python 3.13 and cross-compiles `ICC Editor.exe`; needs `brew install zig`) and `python3 tools/build_macos.py` (compiles `tools/macapp/main.swift`; needs
Apple's Command Line Tools). From a terminal, `python3 -m icc.editor --game-dir <folder>` opens a browser tab instead.

## Quick start

```sh
cd icc-old-db
python3 -m icc.editor
```

Your browser opens at <http://localhost:8002> with the ICC 2002 database from `Original DB/2002/`.

To edit a different database, point the editor at its pair of files:

```sh
python3 -m icc.editor --players "Original DB/2000/dataT.db" --teams "Original DB/2000/dataP.db"
python3 -m icc.editor --players "Original DB/2006/dataT.db" --teams "Original DB/2006/dataP.db"
```

The game version is detected from the files, and the header shows which one is open ("ICC 2006 Editor").

**ICC 1998**, **Australian Cricket Captain** and **ICC 2 (1999)** keep everything in one file, `database.db`, so open it with
`--database` (the game is detected from the file):

```sh
python3 -m icc.editor --database "Original DB/1999/database.db"
```

| Option | Meaning |
|---|---|
| `--players PATH` | player file, `dataT.db` |
| `--teams PATH` | team file, `dataP.db` (use the one that came with the player file) |
| `--database PATH` | ICC 2 (1999) single-file `database.db` (instead of `--players`/`--teams`) |
| `--game 2000\|2002\|2006` | game version; normally detected automatically |
| `--port N` | web port (default 8002) |
| `--no-browser` | don't open a browser tab |

Stop the editor with **Ctrl+C** in the terminal. After updating the code, restart it and refresh the browser tab.

## Using the editor

- **Players**: search, filter by team or nationality, sort by batting or bowling. Edit a player's details, abilities, nationality, role and career records, then press **Apply changes**. The Apply bar stays at the bottom of the screen, and the list keeps its place.
  - Each player has a **Details** tab (identity, abilities, role, fitness) and a **Stats** tab (batting, fielding and bowling career records).
  - **+ New player** creates a player who copies abilities from a template player you choose.
  - **Delete player** also removes them from every squad and picked XI; their place in the XI is filled automatically.
- **Teams**: names; the squad; the picked XI (batting order, captain, keeper, opening bowlers); and club details such as founded year, colour and budgets.
- **Grounds** and **Club records**: ground descriptions and each club's record book.
- **Fixtures** (ICC 2000, 2002 and 2006): shown when `.fxt` fixture files sit next to the databases (ICC 2006: in an `Fxt` folder), or with `--fixtures <folder>`. Pick a file (e.g. `fix1.fxt` = first season, `fixX.fxt` = later seasons, `wc.fxt` = World Cup, `sc*.fxt` = scenarios; ICC 2006 has one per season), then:
  - browse by month, search by team, filter by competition; each fixture shows the ground the game will use;
  - change the date, competition, home/away side, venue, day/night, round or series number; add, duplicate or delete fixtures;
  - **Fixture key**: teams are placed in numbered slots (Championship divisions, National League divisions, international rotation). Swapping two counties' slots moves them between divisions without touching any fixture.
  - Changed fixture files are saved with the databases and get `.bak-<date-time>` copies too. Copy them into the game folder (ICC 2006: `Fxt\`) to play them.

Every **Apply** shows "Applying…" and then a green "✓ Applied" confirmation. Changes are held in the editor until you
press **Save to game files** (top right). **Quit** (top right) closes the editor and warns first if anything is unsaved. Saving:
1. builds both files in the game's encrypted format and checks them in memory (decrypt, re-read, compare) before touching the disk,
2. copies the current files to `dataT.db.bak-<date-time>` and `dataP.db.bak-<date-time>` next to them,
3. writes the new files, then re-reads them from disk as a final check.

### Differences between versions
| | ICC 1998 / Australian Cricket Captain | ICC 2 (1999) | ICC 2000 / 2001 / 2002 | ICC 2006 |
|---|---|---|---|---|
| Files | one `database.db` (not encrypted) | one `database.db` (not encrypted) | `dataT.db` + `dataP.db` | `dataT.db` + `dataP.db` |
| Batting / bowling ability | decimal values | decimal values | slider (fixed steps) | exact decimal value |
| Nationality | any national side | any national side | 2000/2001: Test nations + Scotland/Kenya/Bangladesh; 2002 adds Namibia, Canada, Netherlands | any national side, including the new Associates |
| Career records | 10 types (ACC 13, adding Second XI) | 16 types (batting, bowling, fielding) | 18 types | 24 types, adding six Twenty20 records |
| Club records | none | inside each team: first-class and one-day books | team file | team file |
| Fixtures | editable (`eng98.fxt`; ACC `aus1998.fxt`, `ausX.fxt`) | built into the game (text resources in `Cricket2.exe`) | editable | editable |
| Extra | — | full player names | — | "England central contract" tick box |

### Tips
- **Batting** runs from about 5 to 130, and higher is better. **Bowling** runs from about 27 to 130, and **lower is better**, like a bowling average.
- Always keep a matching pair: `dataT.db` and `dataP.db` from the same database.
- To play your edits, copy both files into the game's install folder, replacing `dataT.db` and `DataP.db` (for ICC 2, just `database.db`). Keep a copy of the originals first.

## Command-line tools

Decrypt and re-encrypt a file, like the old community `convdb.exe`:

```sh
python3 -m icc.convdb -d dataT.db dataT.db1               # decrypt (any version, detected)
python3 -m icc.convdb -e dataT.db1 dataT.db               # encrypt for ICC 2000 / 2002
python3 -m icc.convdb -e dataT.db1 dataT.db --game 2006   # encrypt for ICC 2006
```

Or use the Python modules directly:

```python
from icc import playerfile, teamfile

pf = playerfile.load('Original DB/2002/dataT.db')
p = next(p for p in pf.players if p.name == 'Sachin Tendulkar')
p['batting'] = 480          # raw value, 0..511
playerfile.save(pf, 'dataT.db')
```

## Project layout

```
icc/crypto.py       encryption used by the game (Global.dll)
icc/archive.py      MFC CArchive reader/writer
icc/playerfile.py   dataT.db: players, career records, squads, coaches
icc/teamfile.py     dataP.db: team names, grounds, club records
icc/icc2file.py     ICC 2 (1999) database.db: everything in one file
icc/fixturefile.py  *.fxt fixture files (ICC 2000 / 2002 / 2006)
icc/convdb.py       command-line decrypt/encrypt
icc/editor/         the web editor
Original DB/        databases to edit (1999, 2000, 2002, 2006)
ICC2/, ICC 2000/, ICC 2002/, ICC 2006/  game installs (reference only)
```

## Known issues in the shipped data

- `DataP.db` at the project root and in `ICC 2002/` is corrupted, because it was edited in a text editor at some point. Use `Original DB/2002/dataP.db` instead.
- The "2005 update" database (root `dataT.db`) reused other players' slots and has one duplicate player key. The editor flags the duplicate.
