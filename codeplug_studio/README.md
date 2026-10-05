# Codeplug Studio, version 1

A local web page for editing a QDMR/dmrconf YAML codeplug, with Read and Write buttons
that run `dmrconf`. Tested here against your dm32.yaml with a stand-in for dmrconf.
Not yet tested on Windows or with a real radio.

## One-time setup (MSYS2 MINGW64 shell)

    pacman -S --needed mingw-w64-x86_64-python mingw-w64-x86_64-python-pip
    pacman -S mingw-w64-x86_64-python-ruamel-yaml
    # if pacman does not know that package:
    pip install --break-system-packages ruamel.yaml

## Put the files in place

    mkdir -p ~/codeplug_studio
    cp /c/Users/YOURNAME/Downloads/server.py /c/Users/YOURNAME/Downloads/index.html ~/codeplug_studio/

## Run

    cd ~/codeplug_studio
    python server.py --dmrconf ~/qdmr/build/cli/dmrconf.exe --file ~/qdmr/build/dm32.yaml

Your browser opens http://127.0.0.1:8765/. Press Ctrl+C in the shell to stop.
Options: --com COM3  --radio dm32uv  --workdir FOLDER  --http-port 8765  --no-browser

## What works in version 1

* Channels: edit every column, add FM / DMR / AM channels, insert, delete, move up/down.
* Talk Groups (contacts): edit name, call type, DMR ID, ring.
* Zone: table like the factory CPS (No., Name, Channel Number). Double-click a row, or press
  "Edit members...", to open the Zone Information window: Available Channel and Channel Members
  lists, >> and <<, Up/Down, OK, Cancel, Previous, Next. Limits from QDMR's DM-32UV source:
  250 zones, 64 channels per zone, 16-character names. The "Current Zone/Channel A/B" boxes are
  shown greyed out: your YAML file has no entry for them.
* Scan: table like the factory CPS (No., Name, Scan Number, CTC Scan Mode, Scan TX Mode, Stay Time).
  Double-click a row or press "Edit members..." for the Scan Information window: Scan Name,
  Available Channel and Scan Channel Members lists, Designated Tx Channel (editable), and the
  CTC Scan Mode, Scan TX Mode, Hang Time, Priority Channel 1/2, Priority Sweep Time and Talkback
  controls (greyed: see below). The first entry of a list, "Current Channel" (number 0), is
  QDMR's entry for the radio's current channel; you can remove and re-add it. Limits: 31 lists, 15 entries per list, 11-character names. CTC Scan Mode,
  Scan TX Mode, Stay Time (Hang Time), Priority Sweep Time and Talkback are not in QDMR's YAML,
  so they show as greyed dashes. Priority Channels 1 and 2 are shown but not editable (QDMR
  stores them as a 4-bit number and I could not confirm how the radio reads it).
* Option Feature: click it in the tree to open the twelve-tab "Optional Features" window (Power
  on/off, Alert Tone, Display, GPS, Record, Digital, Work Mode, Other, VFO Scan, Password, Lone
  Work, Menu), laid out like the factory CPS. Only settings that QDMR stores in the YAML are live:
  Power On Interface and display lines, Allow Reset, the alert tones (Radio Silent, Key Tone, SMS
  Alert, Private Call Tone, Call End Tone, Talk Permit Tone, StartUp Sound, Voice Prompt), and on
  the Digital tab SMS Format, Name Data Format, Send TX Name and Private/Group Call Match.
  Call Hold Time, Pre-Carrier Time, VOX Delay and the power-on password switch are shown but not
  editable. All other fields are greyed out because QDMR does not export them.
  With the patched QDMR (qdmr_patch/STEP1.md), 33 more settings become editable when the YAML has a
  complete `settings: dm32uv:` section. A "?" next to a setting means its mapping matches your
  factory CPS but is not yet confirmed on a radio. Without the section these stay greyed out, and
  the bridge refuses to edit them.
  Step 2 adds Time Zone, GPS Mode, Measure Period, Backlight Brightness and Active Wait Time (a
  complete section now has 37 keys; re-decode your YAML with the new build).
  TOT, VOX Level, the two MIC levels and the GPS switch are deliberately left alone: QDMR reads
  values from this radio that differ from what the factory CPS shows for the same data.
* RX Group List, DMR ID: view only. Everything else in your file is kept as is.
* Read, Write, Open, Save, Save as, Import, Export.
* Write does four steps and stops at the first one that fails, before anything is written:
  1. saves the radio's current contents as a raw .bin (data/backups/before-write-DATE.bin);
  2. makes a YAML copy of those same contents (data/backups/before-write-DATE.yaml). It decodes
     the .bin it just saved, with no extra radio traffic. If that fails it reads the radio again
     as YAML. A copy only counts if it loads and has a channels section;
  3. runs `dmrconf verify` on what is about to be written;
  4. writes.
  The YAML copy is the one these tools can write back (use Open, then Write). dmrconf cannot
  write a .bin. The YAML does not hold settings QDMR does not export, but QDMR leaves those alone
  when it writes.
* Under the bar a line says what dmrconf is doing: connecting, scanning the radio's memory (about 20 to
  25 seconds with no percentage, because it probes 200 memory blocks with a 100 ms pause each), reading,
  and for Write: the backup read (its own step), then QDMR reading what is on the radio (first half of
  the Write step), then writing (second half).
* Read and Write show a progress bar with named steps (Write: back up, YAML copy, check, write) and
  the elapsed time. The percentage comes from dmrconf's own progress output. QDMR's write command did
  not report upload progress; the patch in qdmr_patch/ adds one line so that it does. Without that
  patch the bar moves through the steps but stays at the start of the Write step until it finishes.
* Deleting a channel or contact also removes every reference to it (zones, scan lists, etc.).
