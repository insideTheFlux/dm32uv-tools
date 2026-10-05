#!/usr/bin/env python3
"""
mapbatch.py - keeps a log of "I changed this setting, here is what moved in the radio".

  python mapbatch.py add   LOG.json before.bin after.bin --label "VOX level 1 -> 3" [--value 3] [--note "..."]
  python mapbatch.py report LOG.json [--md]
  python mapbatch.py list  LOG.json
  python mapbatch.py scandiff A.json B.json       # two `dm32_read.py scan` files: did any page move?

Use it with dm32_read.py: read the radio, change ONE setting on the radio's own menu, read again,
then `add` the pair. Name steps that change nothing "baseline ..." (two reads in a row with no change):
bytes that move in a baseline are noise (clock, counters) and are left out of the attribution.

The report says, for every setting, which byte(s) and bits moved, with the QDMR field name where QDMR
has one for that byte (settings page 0x4000), and warns when an attribution is not clear.
"""
import argparse
import json
import sys
import time

from whatchanged import parse, owners


def diff_files(a, b):
    A, B = parse(a), parse(b)
    changes = []
    for addr in sorted(set(A) & set(B)):
        for i, (x, y) in enumerate(zip(A[addr], B[addr])):
            if x != y:
                changes.append({"addr": addr, "off": i, "old": x, "new": y})
    return changes, sorted(set(A) - set(B)), sorted(set(B) - set(A))


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"version": 1, "steps": []}


def save(path, doc):
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)


def where(c):
    page = c["addr"] // 0x1000
    return "page 0x%02X offset 0x%03X" % (page, c["off"])


def name_of(c):
    if c["addr"] == 0x4000 and c["off"] < 0x100:
        o = owners(c["off"], c["old"] ^ c["new"])
        return ", ".join(o) if o else "(no QDMR name)"
    return ""


def cmd_add(args):
    doc = load(args.log)
    changes, only_before, only_after = diff_files(args.before, args.after)
    step = {"label": args.label, "value": args.value, "note": args.note, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "before": args.before, "after": args.after, "changes": changes,
            "pages_only_before": only_before, "pages_only_after": only_after}
    doc["steps"].append(step)
    save(args.log, doc)
    print("step %d '%s': %d byte(s) changed" % (len(doc["steps"]), args.label, len(changes)))
    for c in changes[:12]:
        print("   %s: %02X -> %02X  %s" % (where(c), c["old"], c["new"], name_of(c)))
    if len(changes) > 12:
        print("   ... and %d more" % (len(changes) - 12))
    if only_before or only_after:
        print("   note: pages only in the first file: %s, only in the second: %s" % (
            [hex(p // 0x1000) for p in only_before], [hex(p // 0x1000) for p in only_after]))


def analyse(doc):
    noise = set()
    for s in doc["steps"]:
        if s["label"].lower().startswith("baseline"):
            noise |= {(c["addr"], c["off"]) for c in s["changes"]}
    steps = [s for s in doc["steps"] if not s["label"].lower().startswith("baseline")]
    touched = {}
    for s in steps:
        for c in s["changes"]:
            key = (c["addr"], c["off"])
            if key not in noise:
                touched.setdefault(key, []).append((s["label"], c))
    return noise, steps, touched


def verdict(s, real, touched):
    if not real:
        return "NOTHING MOVED: the setting is not stored in the codeplug, or the radio had not saved it yet"
    shared = [c for c in real if len({lab for lab, _ in touched[(c["addr"], c["off"])]}) > 1]
    if len(real) == 1 and not shared:
        return "clear: one byte"
    if shared and len(real) == len(shared):
        return "SHARED: only bytes that other steps also changed (check the bits)"
    if len(real) > 4:
        return "MANY bytes (%d): more than one setting changed, or a counter or checksum moved" % len(real)
    return "%d bytes moved%s" % (len(real), "; some are shared with other steps" if shared else "")


def cmd_report(args):
    doc = load(args.log)
    noise, steps, touched = analyse(doc)
    h = "### " if args.md else ""
    print("%sNoise (moved in baseline reads, ignored): %s" % (h, ", ".join("0x%02X:0x%03X" % (a // 0x1000, o) for a, o in sorted(noise)) or "none"))
    print()
    for n, s in enumerate(steps, 1):
        real = [c for c in s["changes"] if (c["addr"], c["off"]) not in noise]
        val = (" (set to %s)" % s["value"]) if s.get("value") else ""
        print("%s%d. %s%s" % (h, n, s["label"], val))
        print("   %s" % verdict(s, real, touched))
        for c in real[:10]:
            others = sorted({lab for lab, _ in touched[(c["addr"], c["off"])] if lab != s["label"]})
            extra = "   (also changed by: %s)" % ", ".join(others) if others else ""
            print("   - %s: %02X -> %02X (dec %d -> %d), bits %s  %s%s" % (
                where(c), c["old"], c["new"], c["old"], c["new"], format(c["old"] ^ c["new"], "08b"), name_of(c), extra))
        if len(real) > 10:
            print("   ... %d more" % (len(real) - 10))
        if s.get("note"):
            print("   note: %s" % s["note"])
        print()
    # summary table: byte -> settings
    print("%sBytes and the steps that moved them" % h)
    for (addr, off), lst in sorted(touched.items()):
        labs = "; ".join("%s [%02X->%02X]" % (lab, c["old"], c["new"]) for lab, c in lst)
        c0 = lst[0][1]
        print("   0x%02X:0x%03X  %s  <- %s" % (addr // 0x1000, off, name_of(c0) or "-", labs))


def cmd_list(args):
    doc = load(args.log)
    for n, s in enumerate(doc["steps"], 1):
        print("%2d. %-40s %3d byte(s)  %s" % (n, s["label"], len(s["changes"]), s["time"]))


def cmd_scandiff(args):
    a, b = json.load(open(args.a))["ids"], json.load(open(args.b))["ids"]
    moved = [(k, a[k], b[k]) for k in sorted(a) if k in b and a[k] != b[k]]
    gone = sorted(set(a) - set(b))
    new = sorted(set(b) - set(a))
    print("%d logical pages in A, %d in B" % (len(a), len(b)))
    print("moved to another physical block: %s" % (", ".join("%s %s->%s" % m for m in moved) or "none"))
    print("only in A: %s" % (", ".join(gone) or "none"))
    print("only in B: %s" % (", ".join(new) or "none"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("log")
    a.add_argument("before")
    a.add_argument("after")
    a.add_argument("--label", required=True)
    a.add_argument("--value", help="what you set it to on the radio")
    a.add_argument("--note")
    a.set_defaults(fn=cmd_add)
    r = sub.add_parser("report")
    r.add_argument("log")
    r.add_argument("--md", action="store_true")
    r.set_defaults(fn=cmd_report)
    l = sub.add_parser("list")
    l.add_argument("log")
    l.set_defaults(fn=cmd_list)
    sd = sub.add_parser("scandiff")
    sd.add_argument("a")
    sd.add_argument("b")
    sd.set_defaults(fn=cmd_scandiff)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
