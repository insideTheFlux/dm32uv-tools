#!/usr/bin/env python3
"""
Offline tests for dm32_read.py. No radio and no pyserial are needed.

  python test_dm32_read.py                                   # unit tests only
  python test_dm32_read.py --image dm32.bin                  # + a simulated radio holding your dump
  python test_dm32_read.py --capture serial_capture_example.txt
                                                             # + replay of the official CPS's real traffic
                                                             #   (file is in the DM32-Protocol-Spec repo)

What the tests prove:
  * the program can only send read commands (the allow-list rejects a write frame);
  * against a simulated radio holding your dump, it writes back a file identical to your dump;
  * against the real capture, every frame it sends is a frame the official CPS also sent, and the
    pages it returns are the bytes the real radio returned.
"""
import argparse
import re
import sys
import tempfile
import os
import struct
from collections import defaultdict, deque

import dm32_read as d

PASSED, FAILED = 0, 0


def check(ok, what):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print("  ok    " + what)
    else:
        FAILED += 1
        print("  FAIL  " + what)


# ---------------------------------------------------------------------------------------------
# Simulated radio, built from the protocol documents
# ---------------------------------------------------------------------------------------------
VFRAMES = {
    0x01: b"DM32.01.01.046", 0x02: bytes.fromhex("000000000000 15a4 0000 15a4".replace(" ", "")),
    0x03: b"2022-06-27", 0x04: b"D1.01.01.004", 0x05: b"R1.00.01.001",
    0x06: bytes.fromhex("0010200 0ff4f2600".replace(" ", "")), 0x07: bytes.fromhex("00900c00ff9f1400"),
    0x08: bytes.fromhex("00001800ff0f2000"), 0x09: bytes.fromhex("00c06d00ffffff00"),
    0x0A: bytes.fromhex("00100000ff8f0c00"), 0x0B: b"C1.00.01.001", 0x0D: b"", 0x0E: bytes.fromhex("00001500ff5f1700"),
    0x0F: bytes.fromhex("00802700ffbf6d00"), 0x10: bytes.fromhex("50c300"),
}


class FakeRadio:
    """Answers the way the documents say a DP570UV does. `pages` is {logical id: 4096 bytes}."""

    def __init__(self, pages, seed=7):
        import random
        rng = random.Random(seed)
        self.mem = {}                                  # physical block address -> 4096 bytes
        phys = list(range(0x001000, 0x0C9000, 0x1000))   # 200 blocks
        rng.shuffle(phys)
        for pid, data in pages.items():
            self.mem[phys.pop()] = bytearray(data[:0xFFF] + bytes([pid]))
        for p in phys:
            self.mem[p] = bytearray(b"\xff" * 0x1000)
        self.rx = bytearray()
        self.prog = False
        self.received = []

    def write(self, frame):
        self.received.append(bytes(frame))
        f = bytes(frame)
        if f == b"PSEARCH":
            self.rx += b"\x06DP570UV"
        elif f == b"PASSSTA":
            self.rx += b"P\x00\x00"
        elif f == b"SYSINFO":
            self.rx += b"\x06"
        elif f[0] == 0x56 and len(f) == 5:
            payload = VFRAMES[f[4]]
            if f[4] == 0x0D and f[3] == 0x40:
                payload = bytes.fromhex("034e2d00") + bytes(28) + b"\x3f" + bytes(31)
            self.rx += bytes([0x56, f[4], len(payload)]) + payload
        elif f == d.PROGRAM_FRAME:
            self.rx += b"\x06"
        elif f == b"\x02":
            self.rx += b"\xff" * 8
        elif f == b"\x06":
            self.rx += b"\x06"
            self.prog = True
        elif f[0] == 0x52:
            assert self.prog, "read before programming mode"
            addr = int.from_bytes(f[1:4], "little")
            length = f[4] | (f[5] << 8)
            base = addr & ~0xFFF
            data = bytes(self.mem[base][addr - base:addr - base + length])
            assert len(data) == length
            self.rx += b"\x57" + f[1:4] + f[4:6] + data
        else:
            raise AssertionError("simulated radio got an unknown frame %s" % f.hex(" "))

    def read(self, n):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        pass


# ---------------------------------------------------------------------------------------------
# Replay of the official CPS's real traffic
# ---------------------------------------------------------------------------------------------
HEXLINE = re.compile(r"^ {4}((?:[0-9a-f]{2} )+)")


def load_capture(path):
    """Returns a list of (written bytes, read bytes) pairs in capture order."""
    events = []                      # ("W"|"R", bytearray)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Written data" in line:
                events.append(["W", bytearray()])
            elif "Read data" in line:
                events.append(["R", bytearray()])
            else:
                m = HEXLINE.match(line)
                if m and events:
                    events[-1][1] += bytes.fromhex(m.group(1).replace(" ", ""))
    pairs, cur = [], None
    for kind, data in events:
        if kind == "W":
            cur = [bytes(data), bytearray()]
            pairs.append(cur)
        elif cur is not None:
            cur[1] += data
    return [(w, bytes(r)) for w, r in pairs]


class ReplayRadio:
    def __init__(self, pairs):
        self.answers = defaultdict(deque)
        for w, r in pairs:
            self.answers[w].append(r)
        self.rx = bytearray()
        self.unknown = []
        self.received = []

    def write(self, frame):
        f = bytes(frame)
        self.received.append(f)
        q = self.answers.get(f)
        if not q:
            self.unknown.append(f)
            return
        self.rx += q[0] if len(q) == 1 else q.popleft()

    def read(self, n):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        pass


def run_client(transport, argv):
    args = d.build_parser().parse_args(argv)
    sleeps = []
    try:
        pages = args.fn(args, transport=transport, sleep=sleeps.append)
    except d.RadioError as exc:
        return None, str(exc)
    return pages, None


def with_sleep_patched(fn):
    """The commands take a `sleep` argument; the tests pass a no-op so they run instantly."""
    return fn


# ---------------------------------------------------------------------------------------------
def test_unit():
    print("unit tests")
    check(not d.frame_allowed(b"\x57\x00\x10\x00\x00\x10"), "a write frame (0x57) is not allowed")
    check(not d.frame_allowed(b"\x47\x00\x00\x00\x00\x01"), "the 0x47 region probe is not allowed")
    check(not d.frame_allowed(b"\x52\x00\x10\x00\x00\x20"), "a read longer than 4 KB is not allowed")
    check(d.frame_allowed(b"\x52\xff\x1f\x00\x01\x00") and d.frame_allowed(b"PSEARCH") and d.frame_allowed(d.PROGRAM_FRAME),
          "the read frames, handshake and programming-mode frames are allowed")
    guard = d.ReadOnlyGuard(FakeRadio({}))
    try:
        guard.write(b"\x57\x00\x30\x00\x00\x10")
        check(False, "the guard blocks a write frame")
    except d.RadioError:
        check(guard.sent == [], "the guard blocks a write frame before it reaches the port")
    pages = {3: b"\x11" * 0xFFF + b"\x03", 0x67: b"\x22" * 0xFFF + b"\x67"}
    blob = d.dfuse_bytes(pages)
    check(d.parse_dfuse(blob) == pages, "the DfuSe writer and parser agree")
    check(d.parse_ids("0x03-0x05,0x10") == {3, 4, 5, 0x10}, "page lists like 0x03-0x05,0x10 are understood")


def test_image(path):
    print("simulated radio holding %s" % path)
    blob = open(path, "rb").read()
    pages = d.parse_dfuse(blob)
    radio = FakeRadio(pages)
    out = os.path.join(tempfile.mkdtemp(), "out.bin")
    args = d.build_parser().parse_args(["read", "--quiet", "--out", out])
    got = d.cmd_read(args, transport=radio, sleep=lambda s: None)
    check(got == pages, "the pages it read equal the pages of your dump (%d pages)" % len(pages))
    check(open(out, "rb").read() == blob, "the .bin it wrote is byte-for-byte identical to your dump")
    check(all(d.frame_allowed(f) for f in radio.received), "every frame it sent is on the allow-list (%d frames)" % len(radio.received))
    check(not any(f[0] == 0x57 for f in radio.received), "it never sent a write command")
    probes = [f for f in radio.received if f[0] == 0x52 and f[4:6] == b"\x01\x00"]
    reads = [f for f in radio.received if f[0] == 0x52 and f[4:6] == b"\x00\x10"]
    check(len(probes) == 200, "it probed all 200 memory blocks (%d)" % len(probes))
    check(len(reads) == len(pages), "it read each page once (%d reads)" % len(reads))
    # info and scan commands
    args = d.build_parser().parse_args(["scan", "--quiet", "--out", out + ".json"])
    d.cmd_scan(args, transport=FakeRadio(pages), sleep=lambda s: None)
    check(os.path.getsize(out + ".json") > 1000, "the scan command writes the block map")


def test_capture(path):
    print("replay of the official CPS's real capture %s" % path)
    pairs = load_capture(path)
    oem_requests = {w for w, r in pairs}
    # ids and physical addresses as the real radio reported them
    probes = {int.from_bytes(w[1:4], "little") - 0xFFF: r[-1] for w, r in pairs
              if len(w) == 6 and w[0] == 0x52 and w[4:6] == b"\x01\x00" and len(r) == 7}
    check(len(probes) == 200, "the capture holds the real radio's answers to 200 memory probes")
    oem_pages = {}
    for w, r in pairs:
        if len(w) == 6 and w[0] == 0x52 and w[4:6] == b"\x00\x10" and len(r) == 6 + 0x1000:
            addr = int.from_bytes(w[1:4], "little")
            if addr in probes:
                oem_pages[probes[addr]] = r[6:]
    check(len(oem_pages) > 20, "the capture holds %d real 4 KB pages" % len(oem_pages))
    radio = ReplayRadio(pairs)
    ids = ",".join(str(i) for i in sorted(oem_pages))
    out = os.path.join(tempfile.mkdtemp(), "capture.bin")
    args = d.build_parser().parse_args(["read", "--quiet", "--out", out, "--ids", ids])
    got = d.cmd_read(args, transport=radio, sleep=lambda s: None)
    check(not radio.unknown, "every frame the client sent is a frame the official CPS also sent"
          + ("" if not radio.unknown else " (not seen: %s)" % [u.hex(" ") for u in radio.unknown[:3]]))
    check(got == oem_pages, "the pages equal the bytes the real radio returned (%d pages)" % len(got))
    check(all(d.frame_allowed(f) for f in radio.received), "every frame it sent is on the allow-list")
    args = d.build_parser().parse_args(["info"])
    radio2 = ReplayRadio(pairs)
    import io
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        d.cmd_info(args, transport=radio2, sleep=lambda s: None)
    finally:
        sys.stdout = old
    text = buf.getvalue()
    check("DM32.01.01.046" in text and "DP570UV" in text, "info shows the real radio's firmware and model")
    check("0x001000 - 0x0C8FFF" in text and "50000" in text, "info decodes the memory range and the contact count")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="a .bin made by dmrconf or by dm32_read.py")
    ap.add_argument("--capture", help="serial_capture_example.txt from the DM32-Protocol-Spec repository")
    a = ap.parse_args()
    test_unit()
    if a.image:
        test_image(a.image)
    if a.capture:
        test_capture(a.capture)
    print("\n%d passed, %d failed" % (PASSED, FAILED))
    sys.exit(1 if FAILED else 0)
