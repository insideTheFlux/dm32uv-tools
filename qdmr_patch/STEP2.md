# Step 2: the first five settings where QDMR and the factory CPS disagreed

This is added to the same patch file as step 1 (`qdmr-dm32uv-settings.patch` now holds both).
Tested on Linux (GCC 13, Qt 6.4) against QDMR commit `fb9c96a`. **Not compiled on Windows and not
tested on a radio.**

## The five, with your factory screenshot as the reference

| Setting | Raw byte in your dump | Old QDMR reading | New reading = factory CPS | Rule |
|---|---|---|---|---|
| Time Zone | 0x41 = 20 | UTC +9 | UTC +8:00 | UTC offset = raw - 12 (raw 0 to 25 is UTC-12 to UTC+13). **Confirmed on the radio:** writing UTC -1 gave raw 11 and the radio's own menu shows UTC -1:00 |
| GPS Mode | 0x40 bits 2-3 = 2 | Beidou only (read bits 3-4) | GPS+BDS | bits 2-3: 0 GPS, 1 Beidou, 2 GPS+Beidou |
| Measure Period Interval | 0x42 = 5 | 5 minutes | 5 s | raw = seconds, 5 to 255 |
| Backlight Brightness | 0x30 = 4 | 8 (scale 1-10) | 5 | brightness = raw + 1 (1 to 6) |
| Active Wait Time | 0x62 = 12 | 660 ms | 630 ms | ms = (raw - 1) x 30 + 300 (300 to 4800 ms) |

All five rules agree with the factory screenshot AND with NeonPlug's reading of the same bytes
(github.com/infamy/NeonPlug, MIT). Time Zone is now confirmed on hardware (see the table). The other four each rest on **one data point from your radio** plus NeonPlug's
description of the range, so none is fully confirmed. Codeplug Studio marks them with a "?".

## New YAML keys (all in `settings: dm32uv:`, which now has 37 keys)

    utcOffset: 8                 # hours from UTC, -12 to 13
    gpsIntervalSeconds: 5        # 5 to 255
    backlightBrightness: 5       # 1 to 6
    activeWaitTimeMs: 630        # 300 to 4800, steps of 30

The GPS mode stays where QDMR always had it, `settings: gnss: systems:`, and now reads
`[GPS, Beidou]` for your radio. Values outside the ranges above are refused when the YAML is read.

## Things you must know

- **Re-decode your YAML.** The section needs all 37 keys. A section with the 33 keys from step 1 is
  refused (the message names the 4 missing keys). Decode again with the new build.
- **Old YAML files are safe.** A YAML without a `dm32uv:` section (like your current `dm32.yaml`)
  does not write the GPS mode at all, so its old, wrongly read `systems: [Beidou]` cannot change the
  radio. Writing the GPS mode needs the section.
- Changing QDMR's own reading of the GPS mode bits also corrects what QDMR shows for any other file.

## Tests that passed

1. Your dump decodes to utcOffset 8, interval 5, brightness 5, wait 630 and systems GPS + Beidou.
2. With the section unchanged, the written file is byte-for-byte equal to writing without it.
3. Your old `dm32.yaml` writes exactly as before.
4. The 33-key section from step 1 is refused; values 14, 4, 7, 631 and 4830 for the four new keys are refused.
5. Every value of the 38 step 1 and 2 settings was written (178 values; the long ranges were sampled
   at both ends and several points in between). Each changed exactly the intended byte or bits.
6. Setting the GPS systems to GPS or to Beidou through the YAML changed only bits 2-3 of 0x40.
7. Edits made in Codeplug Studio (all five) changed exactly five bytes: 0x30, 0x40, 0x41, 0x42, 0x62.

## Still not done

- **GPS switch: explained.** Your factory file shows it off, QDMR and NeonPlug read it as on. You have
  since turned GPS on, and the radio shipped with it off, so the factory file simply predates that.
  The reading is right. The switch (bit 0 of byte 0x40) is not exported to the YAML yet; it goes
  into the next patch.
- **Work Mode A/B and Main Channel** may be the same story: they can be changed from the radio's
  own keys. QDMR reads raw 1 as VFO for both modes and as B for the main channel. If the radio
  is in VFO mode and B is the main channel today, QDMR is right and the factory file is old.
  Look at the radio's screen to check.
- **TOT, VOX level and the two mic levels** still differ from the factory file. NeonPlug does not
  decode them. Some of them can also be changed on the radio.
- **Time zone and GPS time.** GPS gives the radio UTC time, not your time zone. The Time Zone
  setting (byte 0x41) is a user setting and is UTC +8:00 on your radio now, the factory default.
  Set it to your local offset to get correct local time.

## Confirming the five (a few minutes)

In the factory CPS change one of them (for example the time zone to UTC +5:00), write it to a test
radio, read the radio again with `dmrconf --bin read` and run `test/whatchanged.py before.bin after.bin`.
The tool names the byte that moved. For the time zone you should see byte 0x41 change to 17.

## First write on real hardware (3 Oct 2026)

Codeplug Studio changed Time Zone to UTC -1:00 and wrote it through the patched QDMR. Reading the radio
again and comparing with the pre-write backup showed 480 changed bytes: 479 were exactly the known QDMR
rewrite (the baseline predicted in advance from your dump) and the one other byte was 0x41, 0x14 to
0x0B. The radio's own menu then showed UTC -1:00. So the write path, the encoding of this setting and the
reading of it all agree. What QDMR's rewrite does to the other 479 bytes (mostly channel records) is not yet
checked on the air; see the NeonPlug re-export comparison.

Easy next confirmations you can see on the radio: Backlight Brightness (change 5 to 3 and watch the screen)
and the display colours.
