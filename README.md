# dm32uv-tools

Tools for working with the **Baofeng DM-32UV** (DP570UV) on Windows: a read-only reader, a settings
mapping toolkit, patches for QDMR, and a browser-based codeplug editor.

> **Not official, not affiliated with Baofeng.** Built and tested on one radio (firmware
> `DM32.00.01.048`). Always back up the radio first. You use the writing parts at your own risk.

## What is here

| Folder | What it is | Status |
|---|---|---|
| `dm32_tools/` | `dm32_read.py`: read-only reader (cannot write by construction). `mapbatch.py` + `whatchanged.py`: log which bytes move when you change a setting. `MAPPING_PLAN.md`: the plan. | Tested offline against a simulated radio and the official CPS's recorded traffic. **Not yet run on a real radio.** |
| `qdmr_patch/` | A patch for [QDMR](https://github.com/hmatuschek/qdmr) that exports about 38 more DM-32UV settings, fixes several misread ones, shows write progress, and keeps channel settings QDMR does not model when it updates the radio. Notes in `STEP1.md`, `STEP2.md`, `PRESERVE.md`. | Built and tested on Linux against one dump. Built and ran on Windows (MSYS2); a write of one setting (time zone) was checked on the radio. |
| `codeplug_studio/` | A local web page and small Python bridge that edits a QDMR YAML codeplug (channels, talk groups, zones, scan lists, many settings) and reads and writes the radio through `dmrconf`. Backs up before every write. | Used on one radio for a read and one write. Much of the editing was only tested in a simulated browser. |
| `docs/` | Windows build guide for QDMR and a spreadsheet comparing QDMR, NeonPlug and the factory CPS settings. | |

## Why this exists

QDMR and NeonPlug both read this radio, but they disagree with the factory CPS on several settings,
and QDMR's write reset some channel settings (squelch scale, PTT ID, APRS) on the radio. This project
documents where, fixes what it can, and offers a way to map the rest without writing to the radio.

## Quick start

```
pip install pyserial ruamel.yaml
python dm32_tools/dm32_read.py info                      # firmware and ranges
python dm32_tools/dm32_read.py read --out my.bin         # about 1 minute
python dm32_tools/test_dm32_read.py                      # offline tests
```
QDMR patch: see `qdmr_patch/STEP1.md` (apply with `git apply`, then rebuild) and `docs/WINDOWS_BUILD.md`.
Studio: see `codeplug_studio/README.md`.
Read-only mapping: see `dm32_tools/MAPPING_PLAN.md`.

## Safety

- `dm32_read.py` sends only the handshake, version queries, programming-mode entry and read commands.
  A guard rejects anything else, and the tests check it.
- The QDMR patch and Studio **can write** to the radio. Studio saves a raw `.bin` and a YAML copy of the
  radio's contents before every write and stops if either step fails. `dmrconf` cannot write a `.bin`
  back. The real way back is your factory CPS file.
- Never write the radio's calibration or factory-data blocks. Nothing here does.
- Do not trust a mapping marked "?" until it is confirmed on your radio (see the evidence tables).

## Please do not commit your radio's data

The `.gitignore` excludes `.bin`, `.yaml`, `.neonplug`, `.data` and backup folders. Those files hold your
DMR ID, call sign, contacts, channel names and possibly encryption keys. Check `git status` before every push.

## Credits and licenses

- [QDMR](https://github.com/hmatuschek/qdmr) by Hannes Matuschek and contributors, GPL-3.0. The patches in
  `qdmr_patch/` are changes to QDMR and are therefore GPL-3.0.
- [DM32-Protocol-Spec](https://github.com/infamy/DM32-Protocol-Spec), MIT: the protocol documentation and the
  recorded official-CPS traffic that `dm32_tools/test_dm32_read.py` can replay (download that file yourself).
- [NeonPlug](https://github.com/infamy/NeonPlug), MIT: used as a second reading of the settings block.
- [ruamel.yaml](https://pypi.org/project/ruamel.yaml/), MIT: keeps QDMR's YAML tags and layout when Studio saves.

**License of this repository:** choose **GPL-3.0** when you create it. It is required for the QDMR patches
and keeps the repository simple. This is not legal advice.

## Contributing

Mapping results (byte positions and values only) are the most useful contribution. The protocol spec
and QDMR both take issues and pull requests.
