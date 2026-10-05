# Read-only mapping plan for the DM-32UV

Goal: find out which byte of the radio's memory holds each setting, **without writing anything to
the radio from the PC**. You change a setting on the radio itself, the PC only reads.

The public spec calls this the highest-yield job still open: the settings block (page 0x04) is
about 98% unmapped, and your own tests already left questions: TOT, VOX level, the two mic levels,
Work Mode A/B and Main Channel.

Time: about 3 minutes per step (one read is about a minute). Plan 1.5 hours for the first batch.

## Rules

1. The PC only reads. `dm32_read.py` cannot send a write command (checked by tests, see README).
2. Change settings on the radio's own menu. Do **not** use any reset, factory reset, clear, format or
   delete menu entry, and do not set any password or lock.
3. Keep your first `dm32.bin` and the factory `.data` file untouched.
4. One setting per read unless you follow the "faster" section.
5. Do not switch the radio off or touch the cable during a read. If the radio stops answering,
   switch it off and on and replug the cable. Do not keep retrying in a loop.
6. Skip any step whose menu item your radio does not have.

## 0. Setup (once)

```
pip install pyserial
python dm32_read.py ports            # your cable shows as 1A86:7523
python dm32_read.py info --port COM3 # firmware and ranges, nothing is read from memory
```

**Test the client on the real radio before trusting it.** Read once with QDMR and once with the client,
without touching the radio between the two:

```
dmrconf -D COM3 -R dm32uv --bin read qdmr.bin
python dm32_read.py read --port COM3 --out mine.bin
python whatchanged.py qdmr.bin mine.bin          # expect: 0 byte(s) differ
```

If it says 0 bytes differ, the client reads exactly like QDMR. If not, send me the output.

## 1. Baseline (two reads, no change)

```
python dm32_read.py read --port COM3 --out r0.bin
python dm32_read.py read --port COM3 --out r1.bin
python mapbatch.py add log.json r0.bin r1.bin --label "baseline 1 (no change)"
```

Bytes that move here are noise (clock, counters). Reports leave them out. With GPS on, also do one more
baseline a few minutes later: `... --label "baseline 2 (a few minutes later)"`.

## 2. One step

1. Look at the radio's current value. Write it down.
2. Change that one setting on the radio. Leave the menu so the radio saves it (back to the standby screen).
3. `python dm32_read.py read --port COM3 --out r2.bin`
4. `python mapbatch.py add log.json r1.bin r2.bin --label "VOX level 1 -> 3" --value 3`
5. Next step: use `r2.bin` as "before" and read a new "after". Every read is compared with the one before it.

Each `add` prints what moved. "NOTHING MOVED" means the setting is not in the codeplug, or it needed
leaving the menu first. Repeat once if you are unsure.

## 3. What to change, in this order

**Batch A: settle the open disagreements** (most valuable)

| # | Setting | Values to use | Why |
|---|---|---|---|
| A1 | TOT (transmit time-out) | 30 s, then 90 s | QDMR and the factory CPS differ by one step; two new points give the rule (your dump: raw 22 = 120 s) |
| A2 | VOX level | 1, then 3, then 5 | QDMR reads raw 0 as "off", the factory shows level 1 |
| A3 | VOX delay | 0.5 s, then 2.0 s | confirm the rule |
| A4 | Analog mic level | 1, then 3 | factory shows raw + 1; QDMR rescales to 1-10 |
| A5 | Digital mic level | 1, then 3 | same |
| A6 | Work mode A | switch between channel (MR) and VFO, both ways | QDMR and the factory name them differently |
| A7 | Work mode B | same | |
| A8 | Main channel | A, then B | QDMR and the factory disagree |
| A9 | Backlight brightness | 3, then 6 | confirm raw = value - 1 |
| A10 | GPS switch | off, then on | confirms bit 0 of 0x40 (you know GPS is on now) |
| A11 | GPS mode | each option once | confirms bits 2-3 |

**Batch B: turn the "?" settings into confirmed ones**: toggle each once, in the radio's Alert Tone,
Display and Radio Setting menus:
key tone, SMS alert, group call tone, private call tone, call end tone, talk permit tone,
start-up sound, voice prompt, battery low, volume change prompt, time display, date format,
backlight duration, power save, tail noise reduction, TBST, dual watch mode, record on/off, record type,
roger tone, auto power off, weather alarm, LEDs.
Do them one at a time; they are the checkboxes whose neighbours share a byte.

**Batch C: menu-only items**: language, display mode (A and B), start display, SMS format, FM radio,
sub channel mode, and anything under Radio Setting that is not above.

**Batch D: pages nobody has documented.** Your dump has data in three pages the spec lists as
"never touched by the CPS": 0x51 (a long repeating table that starts `c2 18 c2 18`), 0x56 (sorted
16-bit values) and 0x5A (short records). They may be call logs or caches.
- Read once now. Use the radio normally for a day (some calls if you have DMR), read again, and diff those
  pages: `python mapbatch.py add log.json day0.bin day1.bin --label "a day of use"`.
  If they change, they are runtime data, not settings.
- Never delete a call log on purpose just to test this.

**Batch E (optional, experimental): pages no tool reads.** `python dm32_read.py read --all --out all.bin`
also reads pages such as 0x01 and 0x02 (calibration). Reading is what NeonPlug does for 0x02, but I
have not tried `--all` on a real radio. The output is for looking at, never for writing back. The file
contains this radio's calibration, so look before you share it.

**Batch F (page lifecycle).** `python dm32_read.py scan --out s1.json` takes 12 s and reads no data.
Run it before and after a settings change and compare with `python mapbatch.py scandiff s1.json s2.json`.
It shows whether the radio moves a page to another memory block when it saves (open questions Q2 and Q10).

## 4. Going faster (optional)

Change three unrelated settings per read, each to a **different** value, for example VOX 3, analog mic 2,
digital mic 4. `mapbatch` will show three bytes; match them by value (raw 3 / raw 1 / raw 3 ...). If two
values are the same, or two settings land in the same byte, redo them one at a time. Do not mix settings
you are not sure are independent (for example two tone flags).

## 5. Reading the report

```
python mapbatch.py report log.json
```
For each step it lists the byte and the bits that moved, the QDMR field name if QDMR has one for that
byte (settings page 0x4000 only), and tells you when:
- **clear: one byte**: a clean result;
- **NOTHING MOVED**: not stored in the codeplug, or not saved yet;
- **MANY bytes**: more than one setting moved, or a counter or checksum changed;
- **SHARED**: another step moved the same byte; read the bits.

## 6. Send me the results

Send `log.json` (or the report). It holds byte positions and values, no names or IDs. I will turn it into
a mapping table, update the settings in the QDMR patch and Codeplug Studio, and mark mappings as confirmed.
If you want to contribute upstream, the spec's `CONTRIBUTING.md` and QDMR's issue tracker are the places.
