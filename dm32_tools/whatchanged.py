#!/usr/bin/env python3
"""whatchanged.py OLD.bin NEW.bin [--baseline baseline-qdmr-rewrite.json]

Shows which settings changed between two DM-32UV codeplug files read with `dmrconf --bin read`.
Use it for the confirmation experiments: change ONE setting in the factory CPS, write it to a
test radio, read the radio again, and run this on the before and after files. It prints the
byte and bit that changed and the QDMR field that owns it, so you can see whether QDMR's idea of
that setting matches what the factory CPS really changed.

With --baseline, differences that QDMR makes anyway when it rewrites your dump (about 480 bytes,
mostly normalisation) are counted separately, so only the bytes that are really yours stand out.
Field offsets are those of QDMR's DM-32UV general settings element (settings page at 0x4000).
"""
import struct, sys

FIELDS = {"bootDisplay":["byte",0],"mcuReset":["byte",29],"autoPowerOffDelay":["byte",30],"radioSilent":["bit",32,7,1],"keyTone":["bit",32,6,1],"smsTone":["bit",32,5,1],"groupCallTone":["bit",32,4,1],"privateCallTone":["bit",32,3,1],"eotTone":["bit",32,2,1],"talkPermitTone":["bit",32,1,1],"bootTone":["bit",32,0,1],"voicePrompt":["bit",33,7,1],"lowBatteryTone":["bit",33,6,1],"fmRogerTone":["bit",33,1,2],"displayBrightness":["byte",48],"backlightDuration":["byte",49],"menuDuration":["byte",50],"showVolumeChange":["bit",51,4,1],"dateFormat":["bit",51,3,1],"showClock":["bit",51,0,1],"callColor":["byte",52],"standbyColor":["byte",53],"channelNameAColor":["byte",56],"channelNameBColor":["byte",57],"zoneNameAColor":["byte",58],"zoneNameBColor":["byte",59],"positionFormat":["bit",64,6,1],"gnssMode":["bit",64,2,2],"enableGNSS":["bit",64,0,1],"timeZone":["byte",65],"posUpdatePeriod":["byte",66],"recordMode":["bit",67,2,2],"enableRecording":["bit",69,0,1],"groupCallMatch":["bit",96,1,1],"privateCallMatch":["bit",96,0,1],"dmrCallHangTime":["byte",97],"activeWaitTime":["byte",98],"activeReties":["byte",99],"dmrPreambleDur":["byte",100],"dmrMonitor":["bit",101,7,1],"dmrKill":["bit",101,6,1],"dmrRadioCheck":["bit",101,5,1],"dmrReenable":["bit",101,4,1],"dmrRXAlert":["bit",101,3,1],"smsFormat":["bit",101,1,2],"missedCallNotification":["bit",101,0,1],"dmrRemoteMonitorDuration":["byte",102],"dmrTalkerAliasFormat":["bit",103,4,1],"txTalkerAlias":["bit",103,3,1],"talkerSource":["bit",103,2,1],"dualStandbyMode":["bit",128,6,2],"mainVFO":["bit",128,5,1],"displayModeB":["bit",128,4,1],"displayModeA":["bit",128,3,1],"vfoModeB":["bit",128,2,1],"vfoModeA":["bit",128,1,1],"disableVFOMode":["bit",128,0,1],"dualStandbyHangTime":["byte",129],"sideKeyLock":["bit",133,2,1],"knobLock":["bit",133,1,1],"enableAutoKeyLock":["bit",133,0,1],"autoKeyLockDelay":["byte",134],"sk1Short":["byte",135],"sk1Long":["byte",136],"sk2Short":["byte",137],"sk2Long":["byte",138],"p1Short":["byte",141],"p1Long":["byte",142],"p2Short":["byte",143],"p2Long":["byte",144],"longPressDuration":["byte",148],"transmitTimeout":["byte",160],"totReminder":["byte",161],"voxLevel":["byte",162],"voxDelay":["byte",163],"powerSaveMode":["bit",164,4,2],"weatherAlarm":["bit",164,2,1],"disableLEDs":["bit",164,0,1],"tbstFrequency":["bit",165,4,2],"steMode":["bit",165,0,2],"fmMicLevel":["byte",166],"dmrMicLevel":["byte",167]}   # name -> ["byte", offset] or ["bit", offset, lowest bit, width in bits]

def parse(path):
    d = open(path, "rb").read()
    if d[:5] != b"DfuSe":
        sys.exit("%s is not a DfuSe codeplug file" % path)
    pos = 11 + 11 + 255
    _, n = struct.unpack_from("<II", d, pos); p = pos + 8; pages = {}
    for _ in range(n):
        a, s = struct.unpack_from("<II", d, p); pages[a] = d[p + 8:p + 8 + s]; p += 8 + s
    return pages

def owners(off, changed):
    out = []
    for name, f in FIELDS.items():
        if f[1] != off: continue
        if f[0] == "byte": out.append(name)
        elif changed & (((1 << f[3]) - 1) << f[2]): out.append("%s (bit%s %d%s)" % (name, "s" if f[3] > 1 else "", f[2], "-%d" % (f[2] + f[3] - 1) if f[3] > 1 else ""))
    return out

def diffs(a, b):
    A, B = parse(a), parse(b)
    note = ""
    if set(A) != set(B): note = "Note: the two files hold different pages: %s" % sorted(set(A) ^ set(B))
    out = []
    for addr in sorted(set(A) & set(B)):
        for i, (x, y) in enumerate(zip(A[addr], B[addr])):
            if x != y: out.append((addr, i, x, y))
    return out, note

def describe(addr, i, x, y):
    where = "settings page 0x4000, offset 0x%03X" % i if addr == 0x4000 else "page 0x%06X, offset 0x%03X" % (addr, i)
    line = "%s: %02X -> %02X  (bits changed: %s)" % (where, x, y, format(x ^ y, "08b"))
    if addr == 0x4000 and i < 0x100:
        o = owners(i, x ^ y)
        line += "  QDMR field: " + (", ".join(o) if o else "(no QDMR name for this byte)")
    return line

def main(a, b, baseline=None):
    out, note = diffs(a, b)
    if note: print(note)
    if baseline is None:
        for e in out: print(describe(*e))
        print("%d byte(s) differ." % len(out)); return
    import json
    exp = {(e[0], e[1]): (e[2], e[3]) for e in json.load(open(baseline))["entries"]}
    got = {(e[0], e[1]): (e[2], e[3]) for e in out}
    expected = [k for k in got if exp.get(k) == got[k]]
    beyond = [k for k in got if exp.get(k) != got[k]]
    skipped = [k for k in exp if k not in got]
    print("%d byte(s) differ in total." % len(out))
    print("  %d match the known QDMR rewrite (normalisation QDMR does to your file without any edit)." % len(expected))
    print("  %d are beyond that:" % len(beyond))
    for k in beyond: print("    " + describe(k[0], k[1], *got[k]) + ("   <- differs from the rewrite baseline" if k in exp else ""))
    if skipped: print("  %d byte(s) of the rewrite baseline did not change this time (normal if QDMR skipped unchanged pages)." % len(skipped))

if __name__ == "__main__":
    args = sys.argv[1:]
    base = None
    if "--baseline" in args:
        i = args.index("--baseline"); base = args[i + 1]; del args[i:i + 2]
    if len(args) != 2: sys.exit(__doc__)
    main(args[0], args[1], base)
