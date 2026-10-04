# Reverse-engineering helpers

Scripts used to work out the ICC file formats from the game DLLs. They need
`capstone` and `pefile`, so keep them in a virtualenv outside the repo:

```sh
python3 -m venv /tmp/icc-venv && /tmp/icc-venv/bin/pip install capstone pefile
```

On Windows: `py -3 -m venv %TEMP%\icc-venv` and `%TEMP%\icc-venv\Scripts\pip install capstone pefile`.

Run them from the game folder whose DLLs you are reading (e.g. `cd "ICC 2002"`).

| Script | What it does | Example |
|---|---|---|
| `dismod.py` | Disassemble exported functions, resolving imports, thunks and exports. | `python dismod.py CrManAndEng.dll '?Serialize@CrPlayer@'` |
| `getters.py` | Map a class's `get*/set*/is*` exports to the member offsets/bits they touch — this is how fields get their names. | `python getters.py CrManAndEng.dll CrPlayer` |
| `writes.py` | Summarise a `Serialize` store path: member reads, inline buffer writes, calls. Detects MFC42 vs MFC71 archive offsets. | `python writes.py CrManAndEng.dll '?Serialize@CrBowler@'` |
| `symtrace.py` | Symbolic bit tracer (library). Runs straight-line unpacking code with each bit a set of source bits, so you can see which file bits land in which member bits. | see below |
| `icc2walk.py` | ICC 2 (MFC42) walker: resolves embedded member objects from constructors, lists store paths with virtual `Serialize` calls named. | `python icc2walk.py '?Serialize@CrTeam@@' CrTeam` (run inside `ICC2/`) |

`symtrace` example (load path of `CrPlayer::Serialize` in ICC 2002, reading two
DWORDs `A`, `B` and a BYTE `C` from the archive):

```python
from symtrace import M, show
m = M('CrManAndEng.dll', 0x1002953c, 0x10029850, ['A', 'B', 'C'], {})
m.run()
print(show(m.mem['esi + 0x44']))   # e.g. [0:2]<-B[25:27] [3:3]<-B[24:24] ...
```

## Notes for reading the code
- MFC archive fields: MFC42 (ICC 2, 2000, 2002) `m_lpBufCur`/`m_lpBufMax` at `+0x24`/`+0x28`, flags at `+0x14`;
  MFC71 (ICC 2006) at `+0x28`/`+0x2c`, flags at `+0x18`. `test al,1` on the flags splits store/load.
- MFC42 ordinals seen in the store/load code: `#882`/`#879` CString `<<`/`>>`, `#884`/`#876` COleDateTime `<<`/`>>`
  (DWORD status + double), `#2801` flush (store), `#2740` fill (load), `#3440` CMap GetNextAssoc,
  `#5820` CMapWordToOb::Serialize.
- Abilities in 2000/2002/2006 are scaled by `CrPerson::uintToAbility`: `min + raw*(max-min)/(2^bits-1)`;
  `min`/`max` are CrFixed constants (x4096) pushed just before the call.
