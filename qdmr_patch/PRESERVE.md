# Preserve what you did not change (channels, settings, APRS)

Builds on steps 1 and 2 and the write-progress line. Built and tested on Linux (GCC 13, Qt 6.4)
against your dm32.bin. **Not compiled on Windows (MinGW) and not tried on a radio.**

## What it does

`dmrconf write` updates the radio's codeplug (unless you pass `--init-codeplug`). Before, QDMR
cleared each 48-byte channel record and filled in only what it understands. Everything else
(squelch scale, PTT ID, APRS report mode, other flag bits) came out as QDMR's default.

Now, when it updates the radio, QDMR first decodes what is on the radio. For each channel in the
file it finds the same channel on the radio (same name and receive frequency, or the same slot with
the same name or the same frequency). It encodes the old and the new channel with QDMR's own rules and
changes only the bits that differ between the two, which are the bits you changed. Every other bit
keeps the radio's value. A channel with no match (new, or renamed and retuned at the same time) is
written fresh, as before.

The same rule is used for each channel's TX-contact record, the general settings and the APRS
settings. If QDMR cannot decode the radio's current codeplug it prints a warning and writes as before.

## Tests (your dm32.bin, patched QDMR)

| Test | Result |
|---|---|
| Write with no edits | all 41 channel records identical to the radio's; settings page identical |
| Rename ch3, squelch 5 on ch5 | only the name bytes of ch3 and the squelch byte of ch5 change |
| Swap ch3 and ch5 | both records move intact, 41 identical |
| Insert a channel at the top | the 41 others identical, 1 new |
| Add FM + AM + DMR at the end | 41 identical, 3 new |
| DMR channel given a contact | after writing and decoding again it shows that contact (indexes still right) |
| Time zone edit | one byte beyond the baseline: 0x41 |
| Your old dm32.yaml (no dm32uv section) | settings and channels untouched |
| All 178 values of the 38 step 1-2 settings | each changes exactly the intended bits (0 failures) |

Bytes that differ from your dump with no edit: **479 before, 169 now** (channel pages 0, TX-contact 0,
settings page 0).

## What still changes (169 bytes, all outside channels and settings)

- Contact list (67 bytes): QDMR writes it compactly and adds the "DMR APRS Contact" (456).
- Encryption key slot 4 (60 bytes): the "Encrypt 4" name and its key bytes are cleared.
  QDMR does not decode that slot. Not preserved because keys are a list, not fixed records.
- Leftover text after zone and scan list names (42 bytes): padding.

## Limits

- Contacts, zones, scan lists, RX groups and encryption keys are still rewritten in QDMR's way.
- Your radio is not what dm32.bin holds any more (the old QDMR write, your NeonPlug edits, the time
  zone). Its channels now have squelch 1 and PTT ID off on two channels. This patch keeps what the radio has
  now; it does not bring the old values back. To get squelch 3 back set `squelch: 3` on those channels in the
  YAML (QDMR level 3 writes raw 3). PTT ID and APRS report mode need NeonPlug or the factory CPS.
- Renaming and retuning a channel in the same step makes it a new channel (defaults).

## Found while testing (Codeplug Studio)

The "Add" templates wrote `vox: false`, which QDMR rejects ("bad conversion"). Fixed to `vox: off`
in the new server.py. Before this, a channel added in Studio would have broken Write.

## Applying it

`qdmr-dm32uv-settings.patch` contains everything (steps 1 and 2, the write-progress line and this change).
Apply it to a QDMR checkout and rebuild with the cmake line from `STEP1.md`. If you built an earlier
version, `git apply --check` tells you whether it still fits; start from a clean checkout if it does not.

## Checking on your radio

Because your radio already went through one old rewrite, most of the 169 bytes are already in place.
1. `dmrconf -D COM3 -R dm32uv --bin read before.bin`
2. Write from Codeplug Studio with **no edit** (decode a fresh YAML first).
3. `dmrconf ... --bin read after.bin`, then `python whatchanged.py before.bin after.bin`.

Expect a handful of bytes or none. If you see channel-page changes, send me the output.
`whatchanged.py --baseline` takes a file of the bytes that QDMR rewrites without any edit. Such a file belongs to one dump and holds data from it, so it is not included here.
