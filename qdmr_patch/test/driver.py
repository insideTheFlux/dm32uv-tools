#!/usr/bin/env python3
"""Tests the DM-32UV settings-extension patch against the real dm32.bin."""
import struct, subprocess, sys, os, itertools

# Tests the dm32uv settings extension of the patched QDMR against YOUR dump.
#   usage:  python driver.py DM32.BIN [--quick]
# Needs the test program built from rt.cc (see README in this folder). Set DM32_RT to its path.
import tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from whatchanged import FIELDS
RT = os.environ.get("DM32_RT", "rt")
BIN = next((a for a in sys.argv[1:] if not a.startswith("--")), os.environ.get("DM32_BIN", "dm32.bin"))
QUICK = "--quick" in sys.argv
TMP = tempfile.mkdtemp(prefix="dm32-driver-")
# name -> ('byte', offset) or ('bit', offset, lowest bit), as the code below expects
offs = {k: (("byte", v[1]) if v[0] == "byte" else ("bit", v[1], v[2])) for k, v in FIELDS.items()}

def parse(path):
    d = open(path, 'rb').read()
    assert d[:5] == b'DfuSe'
    pos = 11 + 11 + 255
    tsize, nel = struct.unpack_from('<II', d, pos); p = pos + 8
    out = {}
    for _ in range(nel):
        a, s = struct.unpack_from('<II', d, p); out[a] = d[p+8:p+8+s]; p += 8 + s
    return out

def run(*args):
    r = subprocess.run([RT] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def diff(a, b):
    """list of (address, offset, old, new) for every differing byte, plus address-set differences"""
    A, B = parse(a), parse(b)
    res = []
    if set(A) != set(B):
        res.append(("ADDRESS SET DIFFERS", sorted(set(A) ^ set(B))))
    for addr in sorted(set(A) & set(B)):
        if A[addr] != B[addr]:
            for i, (x, y) in enumerate(zip(A[addr], B[addr])):
                if x != y: res.append((addr, i, x, y))
    return res

# yaml key -> (offset field, width, kind, names)
E = lambda *n: list(n)
COLORS = E('White','Black','Orange','Red','Yellow','Green','Cyan','Blue')
PROPS = {
 'autoPowerOff':      ('autoPowerOffDelay', 8, 'enum', E('Off','Min30','Min60','Hours2','Hours4','Hours8')),
 'fmRogerTone':       ('fmRogerTone', 2, 'enum', E('Off','Beep','BDC')),
 'groupCallTone':     ('groupCallTone', 1, 'bool', None),
 'lowBatteryTone':    ('lowBatteryTone', 1, 'bool', None),
 'showVolumeChange':  ('showVolumeChange', 1, 'bool', None),
 'showClock':         ('showClock', 1, 'bool', None),
 'dateFormat':        ('dateFormat', 1, 'enum', E('YearMonthDay','DayMonthYear')),
 'backlightDuration': ('backlightDuration', 8, 'enum', E('Always','Sec5','Sec10','Sec15','Sec20','Sec25','Sec30','Min1','Min2','Min3','Min4','Min5')),
 'callColor':         ('callColor', 8, 'enum', COLORS),
 'standbyColor':      ('standbyColor', 8, 'enum', COLORS),
 'channelNameAColor': ('channelNameAColor', 8, 'enum', COLORS),
 'channelNameBColor': ('channelNameBColor', 8, 'enum', COLORS),
 'zoneNameAColor':    ('zoneNameAColor', 8, 'enum', COLORS),
 'zoneNameBColor':    ('zoneNameBColor', 8, 'enum', COLORS),
 'positionFormat':    ('positionFormat', 1, 'enum', E('DecimalDegrees','DegreesMinutesSeconds')),
 'recordingEnabled':  ('enableRecording', 1, 'bool', None),
 'recordMode':        ('recordMode', 2, 'enum', E('Receive','Transmit','Both')),
 'activeRetries':     ('activeReties', 8, 'uint', list(range(1, 9))),
 'radioDisableDecode':('dmrKill', 1, 'bool', None),
 'remoteMonitorDecode':('dmrMonitor', 1, 'bool', None),
 'callAlertDecode':   ('dmrRXAlert', 1, 'bool', None),
 'radioEnableDecode': ('dmrReenable', 1, 'bool', None),
 'radioCheckDecode':  ('dmrRadioCheck', 1, 'bool', None),
 'missedCallAlert':   ('missedCallNotification', 1, 'bool', None),
 'onlyChannelMode':   ('disableVFOMode', 1, 'bool', None),
 'displayModeA':      ('displayModeA', 1, 'enum', E('Frequency','ChannelName')),
 'displayModeB':      ('displayModeB', 1, 'enum', E('Frequency','ChannelName')),
 'dualWatchMode':     ('dualStandbyMode', 2, 'enum', E('SingleVFO','DoubleStandby','SingleStandby')),
 'allLEDsDisabled':   ('disableLEDs', 1, 'bool', None),
 'weatherAlarm':      ('weatherAlarm', 1, 'bool', None),
 'powerSave':         ('powerSaveMode', 2, 'enum', E('Off','Percent50','Percent66','Percent75')),
 'tailNoiseReduction':('steMode', 2, 'enum', E('Off','Deg120','Deg180','Hz55')),
 'tbst':              ('tbstFrequency', 2, 'enum', E('Hz1000','Hz1450','Hz1750','Hz2100')),
}

# step 2: key -> (offset field, width, 'lin', (values to test, raw offset: raw = value + off))
LIN = {
 'utcOffset':           ('timeZone', 8, list(range(-12, 14)), 12),
 'gpsIntervalSeconds':  ('posUpdatePeriod', 8, [5, 6, 7, 30, 60, 120, 254, 255], 0),
 'backlightBrightness': ('displayBrightness', 8, list(range(1, 7)), -1),
 'activeWaitTimeMs':    ('activeWaitTime', 8, [300, 330, 630, 1200, 2400, 4770, 4800], None),  # raw = (v-300)/30+1
}
def lin_raw(key, v):
    off = LIN[key][3]
    return (v - 300) // 30 + 1 if key == 'activeWaitTimeMs' else v + off

BLK = open(BIN,'rb').read()
SETTINGS = parse(BIN)[0x4000]

def raw_now(field, width):
    o = offs[field]
    if o[0] == 'byte': return SETTINGS[o[1]]
    return (SETTINGS[o[1]] >> o[2]) & ((1 << width) - 1)

def expected_value_text(key):
    field, width, kind, names = PROPS[key]; raw = raw_now(field, width)
    if kind == 'bool': return 'true' if raw else 'false'
    if kind == 'uint': return str(raw)
    return names[raw] if raw < len(names) else '<out of range raw %d>' % raw


def test_all_values():
    base = os.path.join(TMP, 'ext.bin')
    rc, msg = run('ext', BIN, base)
    if rc != 0:
        print('cannot make the base file:', msg.strip()[:300]); return 1
    bad = 0; total = 0; noop = 0
    for key, (field, width, kind, names) in (list(PROPS.items())[:5] if QUICK else PROPS.items()):
        o = offs[field]; byte = o[1]; bit = 0 if o[0] == 'byte' else o[2]
        mask = (0xff if o[0] == 'byte' else ((1 << width) - 1) << bit)
        values = ['false', 'true'] if kind == 'bool' else (names if kind != 'uint' else [str(x) for x in names])
        if QUICK: values = values[:3]
        for idx, val in enumerate(values):
            raw = idx if kind in ('bool', 'enum') else int(val)
            total += 1
            out = os.path.join(TMP, 't.bin')
            rc, msg = run('set', BIN, out, '%s=%s' % (key, val))
            if rc != 0:
                bad += 1; print("FAIL run", key, val, msg.strip()[:200]); continue
            ds = diff(base, out)
            cur = raw_now(field, width)
            if raw == cur:
                if ds: bad += 1; print("FAIL (value unchanged but bytes differ)", key, val, ds[:3])
                else: noop += 1
                continue
            exp_new = (SETTINGS[byte] & ~mask & 0xff) | ((raw << bit) & mask)
            ok = (len(ds) == 1 and ds[0][0] == 0x4000 and ds[0][1] == byte and ds[0][2] == SETTINGS[byte] and ds[0][3] == exp_new)
            if not ok:
                bad += 1; print("FAIL", key, val, "expected byte 0x%x: %02x -> %02x, got %s" % (byte, SETTINGS[byte], exp_new, ds[:4]))
    # step 2 properties
    for key, (field, width, values, _) in (list(LIN.items())[:2] if QUICK else LIN.items()):
        o = offs[field]; byte = o[1]
        for val in values:
            raw = lin_raw(key, val); total += 1
            rc, msg = run('set', BIN, os.path.join(TMP, 't.bin'), '%s=%s' % (key, val))
            if rc != 0:
                bad += 1; print("FAIL run", key, val, msg.strip()[:200]); continue
            ds = diff(base, os.path.join(TMP, 't.bin')); cur = SETTINGS[byte]
            if raw == cur:
                if ds: bad += 1; print("FAIL (unchanged value but bytes differ)", key, val, ds[:3])
                else: noop += 1
                continue
            ok = (len(ds) == 1 and ds[0][0] == 0x4000 and ds[0][1] == byte and ds[0][2] == cur and ds[0][3] == raw)
            if not ok: bad += 1; print("FAIL", key, val, "expected byte 0x%x: %02x -> %02x, got %s" % (byte, cur, raw, ds[:4]))
    print("checked %d property values: %d unchanged-value no-ops, %d failures" % (total, noop, bad))
    return bad

if __name__ == '__main__':
    sys.exit(1 if test_all_values() else 0)
