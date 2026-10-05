#!/usr/bin/env python3
"""
Codeplug Studio bridge, version 1.

Serves the Codeplug Studio page on http://127.0.0.1:8765 and edits a QDMR YAML
codeplug in place (comments, tags and sections it does not know are kept).
Radio access is done by running QDMR's `dmrconf` program.

Run from the MSYS2 MINGW64 shell:
    python server.py --dmrconf ~/qdmr/build/cli/dmrconf.exe --file dm32.yaml

Needs:  pip install ruamel.yaml   (or the MSYS2 package, see README.md)

Safety:
  * Listens on 127.0.0.1 only and rejects requests from other web pages.
  * Write to radio first saves the radio's current contents to backups/,
    then checks the codeplug with `dmrconf verify`, then writes.
  * Frequencies are checked against the DM-32UV limits taken from QDMR's source
    (FM/DMR 136-174 and 400-480 MHz, AM 118-137 MHz).
"""
import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import re as _re
import threading
import time
import webbrowser
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
except ImportError:
    sys.exit("ruamel.yaml is required: pip install ruamel.yaml")

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------------------
# DM-32UV limits (from QDMR lib/dm32uv_limits.cc)
BANDS = {"fm": [(136, 174), (400, 480)], "dmr": [(136, 174), (400, 480)], "am": [(118, 137)]}
POWERS = ["Low", "High"]
CTCSS = ["67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4", "88.5", "91.5",
         "94.8", "97.4", "100.0", "103.5", "107.2", "110.9", "114.8", "118.8", "123.0",
         "127.3", "131.8", "136.5", "141.3", "146.2", "151.4", "156.7", "159.8", "162.2",
         "165.5", "167.9", "171.3", "173.8", "177.3", "179.9", "183.5", "186.2", "189.9",
         "192.8", "196.6", "199.5", "203.5", "206.5", "210.7", "218.1", "225.7", "229.1",
         "233.6", "241.8", "250.3", "254.1"]
WIDTH_VIEW = {"Narrow": "12.5KHz", "Wide": "25KHz"}
WIDTH_YAML = {v: k for k, v in WIDTH_VIEW.items()}
SLOT_VIEW = {"TS1": "Slot 1", "TS2": "Slot 2"}
SLOT_YAML = {v: k for k, v in SLOT_VIEW.items()}
CALL_TYPES = ["GroupCall", "PrivateCall", "AllCall"]
# Zone limits (QDMR lib/dm32uv_codeplug.hh and dm32uv_limits.cc)
ZONE_MAX = 250
ZONE_MEMBERS_MAX = 64
ZONE_NAME_MAX = 16
# Scan list limits. QDMR stores at most 15 entries per list (anything more is cut off when
# it writes), 31 lists, and 11-character names.
SCAN_MAX = 31
SCAN_MEMBERS_MAX = 15
SCAN_NAME_MAX = 11
SELECTED = "@selected"   # how the page names QDMR's `!selected` entry (the radio's current channel)


def make_yaml():
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.explicit_start = True
    # QDMR writes null as ~
    y.representer.add_representer(
        type(None), lambda r, d: r.represent_scalar("tag:yaml.org,2002:null", "~"))
    return y


YAML_IO = make_yaml()


def load_text(text):
    return YAML_IO.load(text)


def dump_text(doc):
    buf = io.StringIO()
    YAML_IO.dump(doc, buf)
    return buf.getvalue()


def selected_tag():
    """A fresh `!selected ""` scalar, as QDMR writes it."""
    return load_text('x: !selected ""\n')["x"]


class UserError(Exception):
    """A problem the user can fix; shown in the page."""


# ----------------------------------------------------------------------------
# Frequencies and tones
FREQ_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(GHz|MHz|kHz|Hz)?\s*$", re.I)
UNIT = {"ghz": Decimal(1000), "mhz": Decimal(1), "khz": Decimal("0.001"), "hz": Decimal("0.000001")}


def parse_mhz(value):
    m = FREQ_RE.match(str(value))
    if not m:
        raise UserError("Frequency must be a number in MHz, for example 146.52000.")
    try:
        return Decimal(m.group(1)) * UNIT[(m.group(2) or "MHz").lower()]
    except InvalidOperation:
        raise UserError("Frequency must be a number in MHz, for example 146.52000.")


def freq_yaml(mhz):
    text = format(mhz.normalize(), "f")
    return text + " MHz"


def freq_view(value):
    try:
        return "%.5f" % parse_mhz(value)
    except UserError:
        return str(value)


def in_bands(kind, mhz):
    return any(lo <= mhz <= hi for lo, hi in BANDS.get(kind, []))


def band_text(kind):
    return " or ".join("%g-%g MHz" % b for b in BANDS.get(kind, []))


def tone_view(tone):
    if tone is None:
        return "None"
    if "ctcss" in tone:
        return str(tone["ctcss"]).replace(" Hz", "")
    if "dcs" in tone:
        code = str(tone["dcs"])
        return "D%s%s" % (code[1:], code[0].upper())
    return "None"


def tone_yaml(text):
    t = str(text).strip()
    if t == "" or t.lower() in ("none", "off"):
        return None
    if re.fullmatch(r"\d+(\.\d)?", t):
        value = "%.1f" % float(t)
        if value not in CTCSS:
            raise UserError("%s is not a standard CTCSS tone." % t)
        return CommentedMap([("ctcss", value + " Hz")])
    m = re.fullmatch(r"[dD]([0-7]{3})([nNiI])", t) or None
    if m:
        return CommentedMap([("dcs", m.group(2).lower() + m.group(1))])
    m = re.fullmatch(r"([nNiI])([0-7]{3})", t)
    if m:
        return CommentedMap([("dcs", m.group(1).lower() + m.group(2))])
    raise UserError("Use None, a CTCSS tone like 100.0, or a DCS code like D023N.")


# ----------------------------------------------------------------------------
# Channel and contact access
def kind_of(item):
    return next(iter(item))


def body(item):
    """The mapping that holds the fields: typed items (channels, contacts) are wrapped
    as {fm: {...}}, plain items (zones, group lists) are the mapping itself."""
    return item if "id" in item else item[kind_of(item)]


def find_item(seq, item_id):
    for i, item in enumerate(seq):
        if str(body(item)["id"]) == item_id:
            return i, item
    raise UserError("Item %s no longer exists." % item_id)


def channel_view(item):
    kind = kind_of(item)
    ch = item[kind]
    row = {
        "id": str(ch["id"]), "kind": kind, "name": str(ch.get("name", "")),
        "rx": freq_view(ch.get("rxFrequency", "")),
        "tx": freq_view(ch.get("txFrequency", "")),
        "power": str(ch.get("power", "")),
        "rxOnly": bool(ch.get("rxOnly", False)),
        "width": None, "dec": None, "enc": None,
        "cc": None, "slot": None, "contact": None, "group": None,
    }
    if kind == "fm":
        row["width"] = WIDTH_VIEW.get(str(ch.get("bandwidth", "")), "")
        row["dec"] = tone_view(ch.get("rxTone"))
        row["enc"] = tone_view(ch.get("txTone"))
    if kind == "dmr":
        row["width"] = "12.5KHz"
        row["cc"] = int(ch.get("colorCode", 0))
        row["slot"] = SLOT_VIEW.get(str(ch.get("timeSlot", "TS1")), "Slot 1")
        row["contact"] = str(ch["contact"]) if ch.get("contact") is not None else ""
        row["group"] = str(ch["groupList"]) if ch.get("groupList") is not None else ""
    return row


def contact_view(item):
    kind = kind_of(item)
    c = item[kind]
    return {
        "id": str(c["id"]), "kind": kind, "name": str(c.get("name", "")),
        "type": str(c.get("type", "")) if kind == "dmr" else "",
        "number": str(c.get("number", "")),
        "ring": bool(c.get("ring", False)),
    }


def ids_of(seq):
    return [str(body(i)["id"]) for i in seq]


def model(doc):
    contacts = doc.get("contacts", [])
    groups = doc.get("groupLists", [])
    return {
        "version": str(doc.get("version", "")),
        "channels": [channel_view(i) for i in doc.get("channels", [])],
        "contacts": [contact_view(i) for i in contacts],
        "contactOptions": [{"id": str(i[kind_of(i)]["id"]), "name": str(i[kind_of(i)].get("name", ""))}
                           for i in contacts],
        "groupOptions": [{"id": str(g["id"]), "name": str(g.get("name", ""))} for g in groups],
        "zones": [{"id": str(z["id"]), "name": str(z.get("name", "")),
                   "members": [str(c) for c in z.get("A", [])], "b": len(z.get("B", []))}
                  for z in doc.get("zones", [])],
        "limits": {"zones": ZONE_MAX, "zoneMembers": ZONE_MEMBERS_MAX, "zoneName": ZONE_NAME_MAX,
                   "scanLists": SCAN_MAX, "scanMembers": SCAN_MEMBERS_MAX, "scanName": SCAN_NAME_MAX},
        "scanLists": [scan_view(s) for s in doc.get("scanLists", [])],
        "groupLists": [{"name": str(g.get("name", "")), "count": len(g.get("contacts", []))}
                       for g in groups],
        "radioIds": [{"name": str(i[kind_of(i)].get("name", "")), "number": str(i[kind_of(i)].get("number", ""))}
                     for i in doc.get("radioIDs", [])],
        "settings": settings_model(doc),
        "powers": POWERS, "callTypes": CALL_TYPES,
    }


def edit_channel(doc, cid, field, value):
    _, item = find_item(doc["channels"], cid)
    kind = kind_of(item)
    ch = item[kind]
    if field == "name":
        v = str(value).strip()
        if not v:
            raise UserError("A channel needs a name.")
        ch["name"] = v
    elif field in ("rx", "tx"):
        mhz = parse_mhz(value)
        key = "rxFrequency" if field == "rx" else "txFrequency"
        transmit_check = field == "tx" and not bool(ch.get("rxOnly", False))
        if (field == "rx" or transmit_check) and not in_bands(kind, float(mhz)):
            raise UserError("%s MHz is outside this radio's %s range (%s)."
                            % (format(mhz.normalize(), "f"), kind.upper(), band_text(kind)))
        ch[key] = freq_yaml(mhz)
    elif field == "power":
        if value not in POWERS:
            raise UserError("Power must be Low or High.")
        ch["power"] = value
    elif field == "rxOnly":
        ch["rxOnly"] = bool(value)
    elif field == "width":
        if kind != "fm" or value not in WIDTH_YAML:
            raise UserError("Width applies to analog FM channels only.")
        ch["bandwidth"] = WIDTH_YAML[value]
    elif field in ("dec", "enc"):
        if kind != "fm":
            raise UserError("Tones apply to analog FM channels only.")
        ch["rxTone" if field == "dec" else "txTone"] = tone_yaml(value)
    elif field == "cc":
        if kind != "dmr":
            raise UserError("Color code applies to digital channels only.")
        try:
            n = int(str(value).strip())
        except ValueError:
            raise UserError("Color code must be a whole number from 0 to 15.")
        if not 0 <= n <= 15:
            raise UserError("Color code must be from 0 to 15.")
        ch["colorCode"] = n
    elif field == "slot":
        if kind != "dmr" or value not in SLOT_YAML:
            raise UserError("Time slot applies to digital channels only.")
        ch["timeSlot"] = SLOT_YAML[value]
    elif field in ("contact", "group"):
        if kind != "dmr":
            raise UserError("This applies to digital channels only.")
        key = "contact" if field == "contact" else "groupList"
        if value == "":
            if key in ch:
                del ch[key]
        else:
            pool = doc.get("contacts", []) if field == "contact" else doc.get("groupLists", [])
            valid = ids_of(pool)
            if value not in valid:
                raise UserError("Unknown %s." % ("contact" if field == "contact" else "group list"))
            ch[key] = value
    else:
        raise UserError("Unknown field.")
    return channel_view(item)


def edit_contact(doc, cid, field, value):
    _, item = find_item(doc["contacts"], cid)
    kind = kind_of(item)
    c = item[kind]
    if field == "name":
        v = str(value).strip()
        if not v:
            raise UserError("A contact needs a name.")
        c["name"] = v
    elif field == "type":
        if kind != "dmr" or value not in CALL_TYPES:
            raise UserError("Call type applies to DMR contacts only.")
        c["type"] = value
    elif field == "number":
        if kind != "dmr":
            raise UserError("Only DMR contacts can be edited here.")
        try:
            n = int(str(value).strip())
        except ValueError:
            raise UserError("The DMR ID must be a whole number.")
        if not 1 <= n <= 16777215:
            raise UserError("The DMR ID must be from 1 to 16777215.")
        c["number"] = n
    elif field == "ring":
        c["ring"] = bool(value)
    else:
        raise UserError("Unknown field.")
    return contact_view(item)


def zone_view(z):
    return {"id": str(z["id"]), "name": str(z.get("name", "")),
            "members": [str(c) for c in z.get("A", [])], "b": len(z.get("B", []))}


def check_name(value, limit, what):
    v = str(value).strip()
    if not v:
        raise UserError("A %s needs a name." % what)
    if len(v) > limit:
        raise UserError("A %s name can have at most %d characters." % (what, limit))
    return v


def check_zone_name(value):
    return check_name(value, ZONE_NAME_MAX, "zone")


def is_selected_tag(item):
    return str(getattr(item, "tag", "")) == "!selected"


def scan_members(s):
    """Channel ids of a scan list, with QDMR's `!selected` entry shown as '@selected'."""
    out = []
    for item in s.get("channels", []):
        if is_selected_tag(item):
            out.append(SELECTED)
        elif isinstance(item, str) and str(item):
            out.append(str(item))
    return out


def ref_view(value):
    """A channel reference as the page sees it: '' (none), '@selected' or a channel id."""
    if value is None:
        return ""
    if is_selected_tag(value):
        return SELECTED
    return str(value)


def scan_view(s):
    return {"id": str(s["id"]), "name": str(s.get("name", "")), "members": scan_members(s),
            "revert": ref_view(s.get("revert")),
            "primary": ref_view(s.get("primary")), "secondary": ref_view(s.get("secondary"))}


def edit_scan(doc, sid, field, value):
    _, s = find_item(doc["scanLists"], sid)
    if field != "name":
        raise UserError("Unknown field.")
    s["name"] = check_name(value, SCAN_NAME_MAX, "scan list")
    return scan_view(s)


def set_scan(doc, sid, name, members, revert=None):
    """Apply a name, an ordered member list and the designated TX (revert) channel to a scan
    list (the scan dialog's OK). `revert` None leaves the current value alone."""
    _, s = find_item(doc["scanLists"], sid)
    new_name = check_name(name, SCAN_NAME_MAX, "scan list")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise UserError("Bad member list.")
    if len(members) > SCAN_MEMBERS_MAX:
        raise UserError("A scan list holds at most %d entries." % SCAN_MEMBERS_MAX)
    if len(set(members)) != len(members):
        raise UserError("An entry can only be in a scan list once.")
    channel_ids = set(ids_of(doc.get("channels", [])))
    valid = channel_ids | {SELECTED}
    if [m for m in members if m not in valid]:
        raise UserError("Some channels no longer exist. Close the dialog and try again.")
    if revert is not None:
        if not isinstance(revert, str):
            raise UserError("Bad designated TX channel.")
        if revert == "":
            if "revert" in s:
                del s["revert"]
        elif revert == SELECTED:
            if not is_selected_tag(s.get("revert")):
                raise UserError("Current Channel can't be chosen as the designated TX channel here.")
        elif revert in channel_ids:
            s["revert"] = revert
        else:
            raise UserError("The designated TX channel no longer exists. Close the dialog and try again.")
    s["name"] = new_name
    if "channels" not in s:
        s["channels"] = load_text("channels: []\n")["channels"]
    seq = s["channels"]
    del seq[:]
    for m in members:
        seq.append(selected_tag() if m == SELECTED else m)
    return scan_view(s)


def edit_zone(doc, zid, field, value):
    _, z = find_item(doc["zones"], zid)
    if field != "name":
        raise UserError("Unknown field.")
    z["name"] = check_zone_name(value)
    return zone_view(z)


def set_zone(doc, zid, name, members):
    """Apply a name and an ordered member list to a zone in one step (the zone dialog's OK)."""
    _, z = find_item(doc["zones"], zid)
    new_name = check_zone_name(name)
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise UserError("Bad member list.")
    if len(members) > ZONE_MEMBERS_MAX:
        raise UserError("A zone holds at most %d channels." % ZONE_MEMBERS_MAX)
    if len(set(members)) != len(members):
        raise UserError("A channel can only be in a zone once.")
    valid = set(ids_of(doc.get("channels", [])))
    unknown = [m for m in members if m not in valid]
    if unknown:
        raise UserError("Some channels no longer exist. Close the dialog and try again.")
    z["name"] = new_name
    # change the list in place so QDMR's compact [a, b, c] style is kept
    if "A" not in z:
        z["A"] = load_text("A: []\n")["A"]
    seq = z["A"]
    del seq[:]
    for m in members:
        seq.append(m)
    return zone_view(z)


# ----------------------------------------------------------------------------
# Option Feature (the factory "Optional Features" window)
# Only settings that QDMR reads from and writes to the DM-32UV are here. The rest of the
# window is shown greyed out by the page. Fields marked "ro" are shown but not editable.
BOOT_DISPLAYS = ["Image", "Text"]
NAME_FORMATS = ["Iso8", "Unicode"]
SMS_FORMATS = ["Motorola", "Hytera", "DMR"]
BOOT_MSG_MAX = 14

# id: (path in the YAML, kind)
SETTING_FIELDS = {
    "bootDisplay": (("settings", "boot", "display"), "enum", BOOT_DISPLAYS),
    "bootMsg1": (("settings", "boot", "message1"), "text", None),
    "bootMsg2": (("settings", "boot", "message2"), "text", None),
    "allowReset": (("settings", "boot", "allowFactoryReset"), "bool", None),
    "silent": (("settings", "tone", "silent"), "bool", None),
    "keyTone": (("settings", "tone", "keyTone"), "tonebool", None),
    "smsTone": (("settings", "tone", "smsTone"), "bool", None),
    "privateCallTone": (("settings", "tone", "ringtone"), "bool", None),
    "callEndTone": (("settings", "tone", "callEnd"), "typelist", None),
    "talkPermitTone": (("settings", "tone", "talkPermit"), "typelist", None),
    "bootTone": (("settings", "tone", "bootTone"), "bool", None),
    "voicePrompt": (("settings", "audio", "speech"), "bool", None),
    "sendTxName": (("settings", "dmr", "sendTalkerAlias"), "bool", None),
    "nameFormat": (("settings", "dmr", "talkerAliasEncoding"), "enum", NAME_FORMATS),
    "privateMatch": (("settings", "dmr", "privateCallMatch"), "bool", None),
    "groupMatch": (("settings", "dmr", "groupCallMatch"), "bool", None),
    "smsFormat": (("sms", "format"), "enum", SMS_FORMATS),
    # view only
    "callHold": (("settings", "dmr", "groupCallHangTime"), "ro_s", None),
    "preCarrier": (("settings", "dmr", "preamble"), "ro_ms", None),
    "voxDelay": (("settings", "audio", "voxDelay"), "ro_s", None),
    "powerOnPassword": (("settings", "boot", "passwordEnabled"), "ro_bool", None),
}
# Settings exported by the patched QDMR in `settings: dm32uv:` (see qdmr_patch/STEP1.md).
# The names are QDMR's enum names. The section is only used when ALL keys are present: QDMR
# refuses a partial section, because an omitted key would be reset to its default and written.
DM32UV_COLORS = ['White', 'Black', 'Orange', 'Red', 'Yellow', 'Green', 'Cyan', 'Blue']
DM32UV = {
    "autoPowerOff": ("enum", ["Off", "Min30", "Min60", "Hours2", "Hours4", "Hours8"]),
    "fmRogerTone": ("enum", ["Off", "Beep", "BDC"]),
    "groupCallTone": ("bool", None),
    "lowBatteryTone": ("bool", None),
    "showVolumeChange": ("bool", None),
    "showClock": ("bool", None),
    "dateFormat": ("enum", ["YearMonthDay", "DayMonthYear"]),
    "backlightDuration": ("enum", ["Always", "Sec5", "Sec10", "Sec15", "Sec20", "Sec25", "Sec30", "Min1", "Min2", "Min3", "Min4", "Min5"]),
    "callColor": ("enum", DM32UV_COLORS),
    "standbyColor": ("enum", DM32UV_COLORS),
    "channelNameAColor": ("enum", DM32UV_COLORS),
    "channelNameBColor": ("enum", DM32UV_COLORS),
    "zoneNameAColor": ("enum", DM32UV_COLORS),
    "zoneNameBColor": ("enum", DM32UV_COLORS),
    "positionFormat": ("enum", ["DecimalDegrees", "DegreesMinutesSeconds"]),
    "recordingEnabled": ("bool", None),
    "recordMode": ("enum", ["Receive", "Transmit", "Both"]),
    "activeRetries": ("uint", (1, 8)),
    "radioDisableDecode": ("bool", None),
    "remoteMonitorDecode": ("bool", None),
    "callAlertDecode": ("bool", None),
    "radioEnableDecode": ("bool", None),
    "radioCheckDecode": ("bool", None),
    "missedCallAlert": ("bool", None),
    "onlyChannelMode": ("bool", None),
    "displayModeA": ("enum", ["Frequency", "ChannelName"]),
    "displayModeB": ("enum", ["Frequency", "ChannelName"]),
    "dualWatchMode": ("enum", ["SingleVFO", "DoubleStandby", "SingleStandby"]),
    "allLEDsDisabled": ("bool", None),
    "weatherAlarm": ("bool", None),
    "powerSave": ("enum", ["Off", "Percent50", "Percent66", "Percent75"]),
    "tailNoiseReduction": ("enum", ["Off", "Deg120", "Deg180", "Hz55"]),
    "tbst": ("enum", ["Hz1000", "Hz1450", "Hz1750", "Hz2100"]),
    # step 2: where QDMR's earlier reading disagreed with the factory CPS
    "utcOffset": ("int", (-12, 13)),
    "gpsIntervalSeconds": ("uint", (5, 255)),
    "backlightBrightness": ("uint", (1, 6)),
    "activeWaitTimeMs": ("uint", (300, 4800)),
}
for _key, (_kind, _opts) in DM32UV.items():
    SETTING_FIELDS["dm:" + _key] = (("settings", "dm32uv", _key), "dm_" + _kind, _opts)
# GPS mode lives in the common settings (`settings: gnss: systems:`). With the patched QDMR it is
# read from the right bits; older YAML files hold a wrong value, so it is only used with the section.
GPS_MODES = ["GPS", "Beidou", "GPS+Beidou"]
SETTING_FIELDS["dm:gpsMode"] = (("settings", "gnss", "systems"), "dm_gps", GPS_MODES)


def dm32uv_ready(doc):
    """True when the file has a complete `settings: dm32uv:` section."""
    sec = _get(doc, ("settings", "dm32uv"))
    return isinstance(sec, dict) and all(k in sec for k in DM32UV)


INTERVAL_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(min|ms|s)?\s*$")


def _get(doc, path):
    node = doc
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _put(doc, path, value):
    node = doc
    for key in path[:-1]:
        if key not in node or not isinstance(node[key], dict):
            node[key] = CommentedMap()
        node = node[key]
    node[path[-1]] = value


def _interval_seconds(value):
    m = INTERVAL_RE.match(str(value if value is not None else ""))
    if not m:
        return None
    n = float(m.group(1))
    return n * {"min": 60.0, "s": 1.0, "ms": 0.001, None: 0.001}[m.group(2)]


def setting_view(doc, fid):
    path, kind, _ = SETTING_FIELDS[fid]
    if kind.startswith("dm_"):
        if not dm32uv_ready(doc):
            return None
        v = _get(doc, path)
        if kind == "dm_gps":
            names = {str(x) for x in v} if isinstance(v, list) else set()
            return "+".join(n for n in ("GPS", "Beidou") if n in names) if names <= {"GPS", "Beidou"} else ""
        return bool(v) if kind == "dm_bool" else ("%d" % int(v) if kind in ("dm_uint", "dm_int") else str(v))
    v = _get(doc, path)
    if kind in ("bool", "ro_bool"):
        return bool(v)
    if kind == "enum":
        return "" if v is None else str(v)
    if kind == "text":
        return "" if v is None else str(v)
    if kind == "tonebool":
        return v is not None and str(v).strip().lower() not in ("off", "none", "false", "0", "")
    if kind == "typelist":
        return isinstance(v, list) and len(v) > 0
    secs = _interval_seconds(v)
    if secs is None:
        return ""
    return "%.1f" % secs if kind == "ro_s" else "%d" % round(secs * 1000)


def settings_model(doc):
    m = {fid: setting_view(doc, fid) for fid in SETTING_FIELDS}
    m["dm32uvReady"] = dm32uv_ready(doc)
    return m


def apply_settings(doc, changes):
    """Validate every change first, then apply them all (or none)."""
    if not isinstance(changes, dict) or not changes:
        raise UserError("Nothing to change.")
    for fid, value in changes.items():
        if fid not in SETTING_FIELDS:
            raise UserError("Unknown setting.")
        path, kind, options = SETTING_FIELDS[fid]
        if kind.startswith("ro"):
            raise UserError("That setting is view only here.")
        if kind.startswith("dm_"):
            if not dm32uv_ready(doc):
                raise UserError("These settings need the patched QDMR: read the radio with the patched "
                                "dmrconf, or open a YAML file that has a complete 'settings: dm32uv:' section.")
            if kind == "dm_bool" and not isinstance(value, bool):
                raise UserError("Bad value.")
            if kind == "dm_enum" and value not in options:
                raise UserError("Bad choice.")
            if kind == "dm_gps" and value not in options:
                raise UserError("Bad choice.")
            if kind in ("dm_uint", "dm_int"):
                try:
                    n = int(value)
                except (TypeError, ValueError):
                    raise UserError("Bad number.")
                if not options[0] <= n <= options[1]:
                    raise UserError("The value must be from %d to %d." % options)
                if fid == "dm:activeWaitTimeMs" and (n - 300) % 30:
                    raise UserError("The active wait time must be 300 to 4800 ms in steps of 30.")
        if kind in ("bool", "tonebool", "typelist") and not isinstance(value, bool):
            raise UserError("Bad value.")
        if kind == "enum" and value not in options:
            raise UserError("Bad choice.")
        if kind == "text":
            if not isinstance(value, str) or len(value) > BOOT_MSG_MAX:
                raise UserError("The text can have at most %d characters." % BOOT_MSG_MAX)
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise UserError("Use plain letters, numbers and punctuation only.")
    for fid, value in changes.items():
        path, kind, _ = SETTING_FIELDS[fid]
        current = _get(doc, path)
        if kind in ("bool", "enum", "text", "dm_bool", "dm_enum"):
            _put(doc, path, value)
        elif kind in ("dm_uint", "dm_int"):
            _put(doc, path, int(value))
        elif kind == "dm_gps":
            names = value.split("+")
            seq = _get(doc, path)
            if isinstance(seq, list):
                del seq[:]
                seq.extend(names)
            else:
                new = CommentedSeq()
                new.extend(names)
                _put(doc, path, new)
        elif kind == "tonebool":
            if value and not setting_view(doc, fid):
                _put(doc, path, 5)          # QDMR reads an enabled key tone as level 5
            elif not value:
                _put(doc, path, "off")
        elif kind == "typelist":
            if value and not setting_view(doc, fid):
                items = ["DMR", "FM"]
                if isinstance(current, list):
                    del current[:]
                    current.extend(items)
                else:
                    seq = CommentedSeq()
                    seq.extend(items)
                    _put(doc, path, seq)
            elif not value and isinstance(current, list):
                del current[:]
            elif not value:
                _put(doc, path, CommentedSeq())
    return settings_model(doc)


# ----------------------------------------------------------------------------
# Adding, deleting, moving
def next_id(doc, prefix):
    used = set()
    for sect in ("channels", "contacts", "zones", "scanLists", "groupLists"):
        used.update(ids_of(doc.get(sect, [])))
    n = 1
    while "%s%d" % (prefix, n) in used:
        n += 1
    return "%s%d" % (prefix, n)


CHANNEL_TEMPLATES = {
    "fm": """fm:
  id: {id}
  name: {name}
  rxFrequency: 146.52 MHz
  txFrequency: 146.52 MHz
  timeout: !default ""
  rxOnly: false
  vox: off
  squelch: 2
  admit: Always
  rxTone: ~
  txTone: ~
  bandwidth: Narrow
  extended:
    talkaround: false
    reverseBurst: false
  power: Low
""",
    "am": """am:
  id: {id}
  name: {name}
  rxFrequency: 121.5 MHz
  txFrequency: 121.5 MHz
  timeout: !default ""
  rxOnly: true
  vox: off
  squelch: 2
  power: Low
""",
    "dmr": """dmr:
  id: {id}
  name: {name}
  rxFrequency: 438.1 MHz
  txFrequency: 438.1 MHz
  timeout: !default ""
  rxOnly: false
  vox: off
  admit: Always
  colorCode: 1
  timeSlot: TS1
  radioId: !default ""
  extended:
    talkaround: false
    privateCallConfirm: false
    sms: true
    smsConfirm: false
    dataConfirm: false
    dcdm: false
    loneWorker: false
  power: Low
""",
}


def add_item(doc, section, kind, before_id):
    if section == "channels":
        if kind not in CHANNEL_TEMPLATES:
            raise UserError("Unknown channel type.")
        new_id = next_id(doc, "ch")
        name = "Channel %d" % (len(doc["channels"]) + 1)
        item = load_text(CHANNEL_TEMPLATES[kind].format(id=new_id, name=name))
        seq = doc["channels"]
    elif section == "contacts":
        new_id = next_id(doc, "cont")
        text = "dmr:\n  id: %s\n  name: Contact %d\n  ring: false\n  type: GroupCall\n  number: 1\n" % (
            new_id, len(doc["contacts"]) + 1)
        item = load_text(text)
        seq = doc["contacts"]
    elif section == "zones":
        if "zones" not in doc:
            doc["zones"] = load_text("zones: []\n")["zones"]
        if len(doc["zones"]) >= ZONE_MAX:
            raise UserError("This radio holds at most %d zones." % ZONE_MAX)
        new_id = next_id(doc, "zone")
        item = load_text("id: %s\nname: Zone %d\nA: []\nB: []\n" % (new_id, len(doc["zones"]) + 1))
        seq = doc["zones"]
    elif section == "scanLists":
        if "scanLists" not in doc:
            doc["scanLists"] = load_text("scanLists: []\n")["scanLists"]
        if len(doc["scanLists"]) >= SCAN_MAX:
            raise UserError("This radio holds at most %d scan lists." % SCAN_MAX)
        new_id = next_id(doc, "scan")
        item = load_text('id: %s\nname: Scan %d\nrevert: !selected ""\nchannels: []\n' % (new_id, len(doc["scanLists"]) + 1))
        seq = doc["scanLists"]
    else:
        raise UserError("Unknown section.")
    index = len(seq)
    if before_id:
        index, _ = find_item(seq, before_id)
    seq.insert(index, item)
    return new_id


def scrub_refs(node, target, counter):
    """Remove every reference to `target` so no other section points at a deleted item."""
    if isinstance(node, CommentedMap):
        for key in list(node.keys()):
            value = node[key]
            is_plain_str = isinstance(value, str) and type(value).__name__ != "TaggedScalar"
            if key not in ("id", "name") and is_plain_str and str(value) == target:
                if key == "revert" and "channels" not in node:
                    node[key] = selected_tag()
                else:
                    del node[key]
                counter[0] += 1
            else:
                scrub_refs(value, target, counter)
    elif isinstance(node, CommentedSeq):
        i = 0
        while i < len(node):
            value = node[i]
            if isinstance(value, str) and type(value).__name__ != "TaggedScalar" and str(value) == target:
                del node[i]
                counter[0] += 1
            else:
                scrub_refs(value, target, counter)
                i += 1


def delete_item(doc, section, item_id):
    if section not in ("channels", "contacts", "zones", "scanLists"):
        raise UserError("Unknown section.")
    index, _ = find_item(doc[section], item_id)
    del doc[section][index]
    counter = [0]
    scrub_refs(doc, item_id, counter)
    return counter[0]


def move_item(doc, section, item_id, delta):
    seq = doc[section]
    index, _ = find_item(seq, item_id)
    target = index + delta
    if not 0 <= target < len(seq):
        return
    item = seq.pop(index)
    seq.insert(target, item)


# ----------------------------------------------------------------------------
# Application state
class Store:
    def __init__(self, workdir, yaml_path, dmrconf, com, radio):
        self.workdir = workdir
        self.path = yaml_path
        self.dmrconf = dmrconf
        self.com = com
        self.radio = radio
        self.doc = None
        self.dirty = False
        self.log = ""
        self.progress = {"active": False}
        self.lock = threading.Lock()          # protects the document
        self.radio_lock = threading.Lock()    # one radio operation at a time
        (workdir / "backups").mkdir(parents=True, exist_ok=True)
        if yaml_path and yaml_path.exists():
            self.doc = load_text(yaml_path.read_text(encoding="utf-8"))

    def state(self):
        return {
            "file": self.path.name if self.path else "untitled.yaml",
            "loaded": self.doc is not None,
            "dirty": self.dirty,
            "com": self.com, "radio": self.radio,
            "dmrconf": bool(self.dmrconf),
            "backupDir": str(self.workdir / "backups"),
            "log": self.log,
            "model": model(self.doc) if self.doc is not None else None,
        }

    def need_doc(self):
        if self.doc is None:
            raise UserError("No codeplug is open. Use Read to load it from the radio, or Open a YAML file.")

    # --- files
    def save(self):
        self.need_doc()
        if self.path is None:
            raise UserError("Use Save as to choose a file name first.")
        self.path.write_text(dump_text(self.doc), encoding="utf-8")
        self.dirty = False

    def save_as(self, name):
        self.need_doc()
        clean = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip()
        if not clean:
            raise UserError("Please give the file a name.")
        if not clean.lower().endswith((".yaml", ".yml")):
            clean += ".yaml"
        self.path = self.workdir / clean
        self.save()

    def load_from_text(self, text, name):
        try:
            doc = load_text(text)
        except Exception as exc:
            raise UserError("That file is not valid YAML: %s" % str(exc).splitlines()[0])
        if not isinstance(doc, dict) or "channels" not in doc:
            raise UserError("That does not look like a QDMR codeplug (no channels section).")
        self.doc = doc
        self.path = self.workdir / re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name or "opened.yaml").name)
        self.dirty = False

    # --- radio
    ANSI = _re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    BAR = _re.compile(r"^\[[= ]*\]\s*(\d{1,3})%\s*$")

    def progress_begin(self, title, stages):
        """stages: list of (label, weight). Weights are shares of the whole bar."""
        total = float(sum(s[1] for s in stages)) or 1.0
        self.progress = {"active": True, "title": title, "started": time.time(), "step": 0,
                         "step_started": time.time(),
                         "stages": [{"label": s[0], "weight": s[1] / total, "kind": (s[2] if len(s) > 2 else None),
                                     "state": "wait"} for s in stages],
                         "percent": None, "overall": 0.0, "note": ""}
        self.progress["stages"][0]["state"] = "run"

    def progress_stage(self, index, note=""):
        p = self.progress
        if not p.get("active"):
            return
        for i, s in enumerate(p["stages"]):
            s["state"] = "done" if i < index else ("run" if i == index else "wait")
        p["step"] = index; p["percent"] = None; p["note"] = note; p["step_started"] = time.time()
        p["overall"] = sum(s["weight"] for s in p["stages"][:index])

    def progress_percent(self, pct):
        p = self.progress
        if not p.get("active"):
            return
        stage = p["stages"][p["step"]]
        before = sum(s["weight"] for s in p["stages"][:p["step"]])
        # The bar never moves backwards: if dmrconf starts a new pass at 0% (it reads the radio, then
        # writes), keep the highest value reached in this step until the new pass overtakes it.
        reached = max(p.get("reached", 0), pct) if p.get("reached_step") == p["step"] else pct
        p["reached"], p["reached_step"] = reached, p["step"]
        p["percent"] = reached
        p["overall"] = min(1.0, before + stage["weight"] * reached / 100.0)

    def progress_end(self):
        if self.progress.get("active"):
            for s in self.progress["stages"]:
                s["state"] = "done"
            self.progress["overall"] = 1.0
        self.progress["active"] = False

    SCAN_TEXT = ("Scanning the radio's memory first. This takes about 20 to 25 seconds and has no percentage. "
                 "The percentage starts after the scan.")

    def progress_view(self):
        p = dict(self.progress)
        if p.get("active"):
            p["elapsed"] = int(time.time() - p["started"])
            stage = p["stages"][p["step"]]
            pct, kind = p.get("percent"), stage.get("kind")
            if kind in ("read", "write"):
                if pct is None:
                    phase = "Connecting to the radio…"
                elif pct == 0:
                    phase = self.SCAN_TEXT
                elif kind == "read":
                    phase = "Reading the radio · %d%%" % pct
                elif pct <= 50:
                    phase = "Reading what is on the radio first · %d%%" % min(100, pct * 2)
                else:
                    phase = "Writing to the radio · %d%%" % min(100, (pct - 50) * 2)
            else:
                phase = p.get("note") or ""
            p["phase"] = phase
        return p

    def run(self, args, timeout=900):
        """Run dmrconf and read its output while it runs, so the page can show the percentage
        that dmrconf prints on its progress bar."""
        if not self.dmrconf:
            raise UserError("dmrconf was not found. Start the server with --dmrconf PATH.")
        cmd = [str(self.dmrconf)] + args
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        except OSError as exc:
            return -1, "$ %s\nCould not start dmrconf: %s\n" % (" ".join(cmd), exc)
        raw = bytearray()
        state = {"last": -1, "phase_reset": False}

        def reader():
            tail = ""
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                raw.extend(chunk)
                tail = (tail + chunk.decode("utf-8", "replace"))[-400:]
                found = _re.findall(r"(\d{1,3})%", tail)
                if found:
                    pct = max(0, min(100, int(found[-1])))
                    if pct != state["last"]:
                        # a progress bar that starts over means dmrconf moved to its next phase
                        if state["last"] >= 80 and pct <= 20:
                            state["phase_reset"] = True
                        state["last"] = pct
                        self.progress_percent(pct)

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            th.join(2)
            return -1, "$ %s\nTimed out after %d s.\n" % (" ".join(cmd), timeout)
        th.join(5)
        text = self.ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r", "\n")
        lines = [l for l in text.split("\n")]
        keep, last_bar = [], None
        for l in lines:
            if self.BAR.match(l.strip()):
                last_bar = l.strip()
            elif l.strip():
                keep.append(l)
        if last_bar:
            keep.insert(0, last_bar)
        return proc.returncode, "$ %s\n%s\n" % (" ".join(cmd), "\n".join(keep))

    def base_args(self):
        return ["-D", self.com, "-R", self.radio]

    def read_radio(self):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tmp = self.workdir / "radio-read.yaml"
        self.progress_begin("Reading from the radio", [("Read the codeplug", 1.0, "read")])
        try:
            rc, out = self.run(self.base_args() + ["--yaml", "read", str(tmp)])
        finally:
            self.progress_end()
        self.log = out
        if rc != 0 or not tmp.exists():
            raise UserError("Reading the radio failed. See the log for details.")
        text = tmp.read_text(encoding="utf-8")
        shutil.copy(tmp, self.workdir / "backups" / ("read-%s.yaml" % stamp))
        self.load_from_text(text, self.path.name if self.path else "radio-read.yaml")
        self.dirty = False

    def yaml_copy_of(self, bin_path, yaml_path):
        """Make a YAML copy of the radio's current contents. First decode the .bin we just saved
        (no radio traffic, and it is the same bytes); if that fails, read the radio again as YAML.
        Returns (ok, log text). A copy only counts if it parses and has a channels section."""
        def good():
            try:
                if not yaml_path.exists() or yaml_path.stat().st_size == 0:
                    return False
                doc = load_text(yaml_path.read_text(encoding="utf-8"))
                return isinstance(doc, dict) and isinstance(doc.get("channels"), list)
            except Exception:
                return False

        rc, out = self.run(["-R", self.radio, "decode", str(bin_path), str(yaml_path)])
        log = "Decode the saved .bin into YAML (no radio traffic)\n" + out
        if rc == 0 and good():
            return True, log
        if yaml_path.exists():
            yaml_path.unlink()
        rc, out = self.run(self.base_args() + ["--yaml", "read", str(yaml_path)])
        log += "\nThat did not work, so reading the radio again as YAML\n" + out
        if rc == 0 and good():
            return True, log
        if yaml_path.exists():
            yaml_path.unlink()
        return False, log

    def write_radio(self):
        """Back up, check, then write. Any failed step stops everything before the write."""
        self.need_doc()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = self.workdir / "backups"
        bin_backup = folder / ("before-write-%s.bin" % stamp)
        yaml_backup = folder / ("before-write-%s.yaml" % stamp)
        steps = []
        self.progress_begin("Writing to the radio", [("Back up the radio (.bin)", 0.33, "read"), ("Save a YAML copy", 0.02),
                                                     ("Check against the radio's limits", 0.03), ("Write", 0.62, "write")])
        try:
            return self._write_steps(stamp, folder, bin_backup, yaml_backup, steps)
        finally:
            self.progress_end()

    def _write_steps(self, stamp, folder, bin_backup, yaml_backup, steps):
        # 1. the radio's current contents, as raw bytes
        rc, out = self.run(self.base_args() + ["--bin", "read", str(bin_backup)])
        steps.append("STEP 1: back up the radio's current contents (.bin)\n" + out)
        if rc != 0 or not bin_backup.exists():
            self.log = "\n".join(steps)
            raise UserError("Could not back up the radio, so nothing was written. See the log.")

        # 2. the same contents as a YAML file, which is the copy these tools can write back
        self.progress_stage(1)
        ok, out = self.yaml_copy_of(bin_backup, yaml_backup)
        steps.append("STEP 2: keep a YAML copy of the radio's current contents\n" + out)
        if not ok:
            self.log = "\n".join(steps)
            raise UserError("Could not make a YAML copy of the radio's current contents, so nothing was "
                            "written. The raw backup %s was saved. See the log." % bin_backup.name)

        # 3. check what we are about to write
        self.progress_stage(2)
        pending = self.workdir / "pending-write.yaml"
        pending.write_text(dump_text(self.doc), encoding="utf-8")
        rc, out = self.run(["-R", self.radio, "verify", str(pending)])
        steps.append("STEP 3: check the codeplug against the radio's limits\n" + out)
        if rc != 0:
            self.log = "\n".join(steps)
            raise UserError("The codeplug did not pass the radio's checks, so nothing was written. "
                            "Your backups %s and %s were saved. See the log." % (yaml_backup.name, bin_backup.name))

        # 4. write
        self.progress_stage(3)
        rc, out = self.run(self.base_args() + ["write", str(pending)])
        steps.append("STEP 4: write to the radio\n" + out)
        self.log = "\n".join(steps)
        if rc != 0:
            raise UserError("Writing to the radio failed. See the log. The radio's old contents are saved as %s "
                            "and %s." % (yaml_backup.name, bin_backup.name))
        return {"yaml": yaml_backup.name, "bin": bin_backup.name}


STORE = None
INDEX_HTML = HERE / "index.html"


# ----------------------------------------------------------------------------
# HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "CodeplugStudio/1"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML.read_bytes(), "text/html")
        if path == "/api/progress":
            return self._json(STORE.progress_view())
        if path == "/api/state":
            with STORE.lock:
                return self._json(STORE.state())
        if path == "/api/export":
            with STORE.lock:
                if STORE.doc is None:
                    return self._send(404, "no codeplug", "text/plain")
                name = STORE.path.name if STORE.path else "codeplug.yaml"
                return self._send(200, dump_text(STORE.doc), "application/x-yaml",
                                  {"Content-Disposition": 'attachment; filename="%s"' % name})
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-CPS") != "1":
            return self._send(403, "forbidden", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "Bad request."}, 400)
        route = self.path.split("?")[0]
        try:
            if route in ("/api/read", "/api/write"):
                if not STORE.radio_lock.acquire(blocking=False):
                    raise UserError("The radio is busy with another operation.")
                try:
                    if route == "/api/read":
                        STORE.read_radio()
                        return self._json({"ok": True, "state": STORE.state()})
                    backups = STORE.write_radio()
                    return self._json({"ok": True, "backup": backups["bin"], "backups": backups, "state": STORE.state()})
                finally:
                    STORE.radio_lock.release()
            with STORE.lock:
                result = self._handle(route, req)
            return self._json(result)
        except UserError as exc:
            with STORE.lock:
                state_log = STORE.log
            return self._json({"ok": False, "error": str(exc), "log": state_log})
        except Exception as exc:  # keep the server alive, tell the page
            return self._json({"ok": False, "error": "Internal error: %s" % exc}, 500)

    def _handle(self, route, req):
        s = STORE
        if route == "/api/edit":
            s.need_doc()
            fn = {"channels": edit_channel, "contacts": edit_contact, "zones": edit_zone, "scanLists": edit_scan}.get(req["section"])
            if fn is None:
                raise UserError("Unknown section.")
            row = fn(s.doc, req["id"], req["field"], req["value"])
            s.dirty = True
            return {"ok": True, "row": row}
        if route == "/api/settings/set":
            s.need_doc()
            apply_settings(s.doc, req.get("changes"))
            s.dirty = True
            return {"ok": True, "state": s.state()}
        if route == "/api/scan/set":
            s.need_doc()
            row = set_scan(s.doc, req["id"], req.get("name", ""), req.get("members", []), req.get("revert"))
            s.dirty = True
            return {"ok": True, "row": row, "state": s.state()}
        if route == "/api/zone/set":
            s.need_doc()
            row = set_zone(s.doc, req["id"], req.get("name", ""), req.get("members", []))
            s.dirty = True
            return {"ok": True, "row": row, "state": s.state()}
        if route == "/api/add":
            s.need_doc()
            new_id = add_item(s.doc, req["section"], req.get("kind", "fm"), req.get("before"))
            s.dirty = True
            return {"ok": True, "id": new_id, "state": s.state()}
        if route == "/api/delete":
            s.need_doc()
            n = delete_item(s.doc, req["section"], req["id"])
            s.dirty = True
            return {"ok": True, "scrubbed": n, "state": s.state()}
        if route == "/api/move":
            s.need_doc()
            move_item(s.doc, req["section"], req["id"], int(req["delta"]))
            s.dirty = True
            return {"ok": True, "state": s.state()}
        if route == "/api/save":
            s.save()
            return {"ok": True, "state": s.state()}
        if route == "/api/saveas":
            s.save_as(req.get("name", ""))
            return {"ok": True, "state": s.state()}
        if route == "/api/load":
            s.load_from_text(req.get("text", ""), req.get("name", ""))
            return {"ok": True, "state": s.state()}
        raise UserError("Unknown request.")


def find_dmrconf(arg):
    if arg:
        p = Path(arg).expanduser()
        return p if p.exists() else None
    found = shutil.which("dmrconf")
    if found:
        return Path(found)
    for guess in (HERE.parent / "qdmr" / "build" / "cli" / "dmrconf.exe",
                  Path.home() / "qdmr" / "build" / "cli" / "dmrconf.exe"):
        if guess.exists():
            return guess
    return None


def main():
    global STORE
    ap = argparse.ArgumentParser(description="Codeplug Studio bridge")
    ap.add_argument("--dmrconf", help="path to dmrconf(.exe)")
    ap.add_argument("--file", help="YAML codeplug to open at start (default: dm32.yaml if present)")
    ap.add_argument("--com", default="COM3", help="serial port of the radio (default COM3)")
    ap.add_argument("--radio", default="dm32uv", help="dmrconf radio key (default dm32uv)")
    ap.add_argument("--workdir", default=str(HERE / "data"), help="folder for saved files and backups")
    ap.add_argument("--http-port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    workdir = Path(a.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    yaml_path = Path(a.file).expanduser().resolve() if a.file else workdir / "dm32.yaml"
    dmrconf = find_dmrconf(a.dmrconf)
    STORE = Store(workdir, yaml_path, dmrconf, a.com, a.radio)

    print("Codeplug Studio")
    print("  codeplug file : %s (%s)" % (yaml_path, "loaded" if STORE.doc is not None else "not found yet"))
    print("  dmrconf       : %s" % (dmrconf or "NOT FOUND (editing works, Read/Write need --dmrconf)"))
    print("  radio         : %s on %s" % (a.radio, a.com))
    print("  backups in    : %s" % (workdir / "backups"))
    url = "http://127.0.0.1:%d/" % a.http_port
    print("Open %s   (Ctrl+C to stop)" % url)

    httpd = ThreadingHTTPServer(("127.0.0.1", a.http_port), Handler)
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
