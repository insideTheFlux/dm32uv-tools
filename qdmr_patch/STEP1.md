# Step 1: export the settings where QDMR and the factory CPS agree

Tested against QDMR commit `fb9c96af53f646c0706fa360209b2e0e6cd79a08` (version 0.15.1). Check
yours with `git log -1` in your `qdmr` folder; if it differs, `git apply --check` will say whether
the patch still fits. Built and tested here on Linux (GCC 13, Qt 6.4). **Not compiled on Windows (MinGW) and
not tested on a radio.**

## What the patch does

QDMR already reads about 83 DM-32UV general settings from the radio but only copies about 25 into
the YAML file. The patch adds a new section, `settings: dm32uv:`, with 33 more settings,
named like the tabs of the factory "Optional Features" window. Example from your dump:

    settings:
      dm32uv:
        autoPowerOff: Off
        callColor: Cyan
        backlightDuration: Sec5
        powerSave: Percent75
        ...

When writing, a field is written **only if its value differs from what the radio holds now**. A
file without the section, or with unchanged values, leaves every byte as it is. A section with
only some of the keys is **refused** with a message naming the missing keys: an omitted key would
otherwise be set to its default and then written over the radio's value. Values the radio
holds outside the known ranges are left alone and show as defaults in the YAML.

Not in step 1 (they need the confirmation experiments, see below): TOT, Active Wait Time, VOX Level,
mic levels, brightness, time zone, GPS switch and mode, Work Mode A/B, Main Channel, Menu Exit Time,
Remote Monitor Time, Dual Watch Hang Time, TOT Pre Time.

## Tests that passed (with your dm32.bin)

1. Decoding gives the new section with values matching the raw bytes. Everything else in the
   YAML is identical to the YAML you got from unpatched QDMR (apart from how this sandbox's
   yaml-cpp spells `!default`).
2. Encoding on top of your dump with the section **unchanged** gives a file byte-for-byte equal to
   encoding without the section.
3. Every value of every new setting (131 in all) was written one at a time. Each changed exactly the
   intended byte or bits in the settings page and nothing else.
4. Editing values in the YAML (4 settings) changed exactly the expected 3 bytes.
5. A YAML without the section (such as your current `dm32.yaml`) writes exactly as before.
6. An unknown name such as `callColor: Purple` is refused with a message listing the valid names.
   `dmrconf verify` refuses `activeRetries: 99`.
7. A dump whose raw bytes hold out-of-range values (made by hand) decodes without errors and those
   bytes are left alone on write unless you change that setting.
8. A section with two keys deleted is refused, and no output file is written.
9. Edits made in Codeplug Studio (4 settings), saved to YAML and encoded by the patched QDMR onto
   your dump, changed exactly the expected 3 bytes.

## How sure are we of each mapping?

The mapping agrees with your factory screenshots, but only for the values in your dump:

- **Strong (10)**: a distinctive value that matches the factory label on its own.
- **Moderate (14)**: matches, but neighbouring flags in the same byte have the same value, so
  two settings could be swapped without our noticing.
- **Weak (9)**: the value is off/zero, so any other off setting nearby would match too.

A wrong mapping would write a different but ordinary setting (for example a tone flag), which you
can set back. It is not the kind of mistake that bricks a radio. Still, confirm the Moderate and
Weak ones before relying on them.

| YAML key | Factory label | Byte (page 0x4000) | Bits | Evidence |
|---|---|---|---|---|
| `autoPowerOff` | Auto Power Off | 0x1E | all | Weak |
| `fmRogerTone` | Analog Roger Tone | 0x21 | bits 1-2 | Weak |
| `groupCallTone` | Group Call Tone | 0x20 | bit 4 | Moderate |
| `lowBatteryTone` | Battery Low | 0x21 | bit 6 | Moderate |
| `showVolumeChange` | Volume Change Prompt | 0x33 | bit 4 | Moderate |
| `showClock` | Time Display | 0x33 | bit 0 | Moderate |
| `dateFormat` | Date Display Format | 0x33 | bit 3 | Moderate |
| `backlightDuration` | Auto Backlight Duration | 0x31 | all | Strong |
| `callColor` | Call Display Color | 0x34 | all | Strong |
| `standbyColor` | Standby Character Color | 0x35 | all | Strong |
| `channelNameAColor` | A Channel Name Color | 0x38 | all | Strong |
| `channelNameBColor` | B Channel Name Color | 0x39 | all | Strong |
| `zoneNameAColor` | Zone A Color | 0x3A | all | Strong |
| `zoneNameBColor` | Zone B Color | 0x3B | all | Strong |
| `positionFormat` | GPS Display Format | 0x40 | bit 6 | Moderate |
| `recordingEnabled` | Record Switch | 0x45 | bit 0 | Weak |
| `recordMode` | Record Type | 0x43 | bits 2-3 | Weak |
| `activeRetries` | Active Retries Time | 0x63 | all | Strong |
| `radioDisableDecode` | Radio Disable Decode | 0x65 | bit 6 | Weak |
| `remoteMonitorDecode` | Remote Monitor Decode | 0x65 | bit 7 | Weak |
| `callAlertDecode` | Call Alert Decode | 0x65 | bit 3 | Moderate |
| `radioEnableDecode` | Radio Enable Decode | 0x65 | bit 4 | Weak |
| `radioCheckDecode` | Radio Check Decode | 0x65 | bit 5 | Moderate |
| `missedCallAlert` | Missed Call Alert | 0x65 | bit 0 | Moderate |
| `onlyChannelMode` | Only Channel Mode | 0x80 | bit 0 | Weak |
| `displayModeA` | A Channel Display Mode | 0x80 | bit 3 | Moderate |
| `displayModeB` | B Channel Display Mode | 0x80 | bit 4 | Moderate |
| `dualWatchMode` | Dual Watch Mode | 0x80 | bits 6-7 | Moderate |
| `allLEDsDisabled` | Disable All LEDs | 0xA4 | bit 0 | Weak |
| `weatherAlarm` | Weather Alarm | 0xA4 | bit 2 | Moderate |
| `powerSave` | Power Save | 0xA4 | bits 4-5 | Moderate |
| `tailNoiseReduction` | Tail Noise Reduction | 0xA5 | bits 0-1 | Strong |
| `tbst` | TBST | 0xA5 | bits 4-5 | Strong |

## Confirming the mappings (a few minutes per batch)

1. Read the radio: `dmrconf -D COM3 -R dm32uv --bin read before.bin`
2. In the factory CPS, change **one** setting to a different value and write it (test radio first).
3. Read again to `after.bin`, then run `python test/whatchanged.py before.bin after.bin`.

It prints the byte, the bits and the QDMR field that owns them. If the field is the one you
changed, the mapping is confirmed. You may change several unrelated settings at once, as long as
they are in different bytes.

## Applying the patch (MSYS2 MINGW64 shell)

Copy `qdmr-dm32uv-settings.patch` into your MSYS2 home folder, then:

    cd ~/qdmr
    git apply --check ~/qdmr-dm32uv-settings.patch    # prints nothing if it fits
    git apply ~/qdmr-dm32uv-settings.patch
    cd build
    cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DINSTALL_APPSTREAM_DATA=OFF \
          -DCMAKE_CXX_FLAGS="-DNOGDI -DNOMINMAX"
    ninja

(The cmake line is needed because a new source file was added.) Then check it worked:

    ./cli/dmrconf.exe -R dm32uv decode ~/dm32.bin ~/decoded.yaml

`decoded.yaml` should contain a `dm32uv:` section under `settings:` with 33 keys. Codeplug Studio
makes those settings editable only for a file that has the complete section.

## Things found along the way (not caused by this patch)

- Writing the unpatched QDMR re-encodes the codeplug and changes about 480 bytes of your dump in 8
  pages (channels, contacts, zones and similar). In the settings page, 5 bytes change: 3 are stray
  bytes after the boot text (set to 0), and 2 are the APRS destination ID, 456, which is set to 0
  because no channel uses the APRS system (my reading of the code, not confirmed on a radio). If you
  rely on APRS, check that setting after a write.
- The DM-32UV upload always reads the radio's current codeplug first and writes into it, so fields
  QDMR does not set are kept.
- `dmrconf verify` and `decode` print a warning that DM-32UV support is under development.

## Files

- `qdmr-dm32uv-settings.patch`: the patch (new `lib/dm32uv_extension.*`, changes to
  `radiosettings`, `dm32uv_codeplug`, `dm32uv_limits`, `CMakeLists.txt`).
- `test/rt.cc`, `test/driver.py`: the round-trip tests. They need Qt 6 and a Linux build.
- `test/whatchanged.py`: the confirmation tool above. Needs only Python.
