#!/usr/bin/env python3
"""
dm32_read.py - a READ-ONLY reader for the Baofeng DM-32UV (DP570UV) codeplug.

It only ever sends the commands needed to read: the handshake (PSEARCH, PASSSTA, SYSINFO), version
queries (V-frames), the three-step "programming mode" entry that every tool needs before it may read,
and 0x52 memory reads. The write command (0x57) does not exist in this program, and a guard in the
transport rejects any frame that is not on the allow-list, so it cannot write by accident.

What it writes to disk is a DfuSe file in the same format as `dmrconf --bin read`, so QDMR's `decode`
and the whatchanged.py / mapbatch.py tools can use it.

Protocol, delays and frame formats come from the public DM32-Protocol-Spec (docs 02 and 03) and were
checked against a capture of the official CPS (see test_dm32_read.py).

Commands:
  ports                 list serial ports (the radio's cable shows up as 1A86:7523 or 067B:23A3)
  info                  connect, print the radio's version information, disconnect
  scan                  list which memory block holds which logical page (about 12 s), no data read
  read                  read the codeplug pages and write a .bin   (about 1 minute)

Needs:  pip install pyserial        (not needed for the offline tests)
"""
import argparse
import json
import struct
import sys
import time
import zlib
from dataclasses import dataclass, replace

BAUD = 115200
BLOCK = 0x1000
DEFAULT_IDS = (0x03, 0x67)           # logical pages 0x03..0x67 = the range QDMR reads (0x3000..0x67FFF)
VFRAME_IDS = (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F, 0x10)
VFRAME_NAMES = {
    0x01: "firmware", 0x02: "unknown 02", 0x03: "build date", 0x04: "DSP version", 0x05: "radio version",
    0x06: "range 06", 0x07: "range 07", 0x08: "range 08", 0x09: "range 09", 0x0A: "main config range",
    0x0B: "codeplug version", 0x0D: "unknown 0D", 0x0E: "boot image range", 0x0F: "contacts range",
    0x10: "max contacts",
}


class RadioError(Exception):
    pass


@dataclass
class Timing:
    """Delays in seconds, from the spec (they are known to be sufficient, not known to be minimal)."""
    init: float = 0.400            # after opening the port
    clear: float = 0.200           # after flushing; applied twice
    psearch: float = 0.150         # after PSEARCH, before reading the answer
    handshake: float = 0.050       # between handshake steps
    vframe: float = 0.050          # around each V-frame
    program: float = 0.010         # between the programming-mode steps
    read_wait: float = 0.025       # after a 0x52 request, before the answer
    read_settle: float = 0.030     # after the payload
    probe_gap: float = 0.005       # between 1-byte probes
    block_gap: float = 0.150       # between 4 KB reads
    reopen: float = 0.400

    def scaled(self, k):
        return Timing(**{f: getattr(self, f) * k for f in self.__dataclass_fields__})


# ---------------------------------------------------------------------------------------------
# The allow-list: the only frames this program may ever send.
# ---------------------------------------------------------------------------------------------
PROGRAM_FRAME = b"\xff\xff\xff\xff\x0cPROGRAM"


def frame_allowed(f):
    if f in (b"PSEARCH", b"PASSSTA", b"SYSINFO", PROGRAM_FRAME, b"\x02", b"\x06"):
        return True
    if len(f) == 5 and f[0] == 0x56 and f[1] == 0 and f[2] == 0 and f[3] in (0x00, 0x40) and 1 <= f[4] <= 0x10:
        return True                                           # V-frame query
    if len(f) == 6 and f[0] == 0x52:                          # read: 0x52 <addr:3 LE> <len:2 LE>
        length = f[4] | (f[5] << 8)
        return 1 <= length <= BLOCK
    return False


class ReadOnlyGuard:
    """Wraps a transport and refuses to pass on anything that is not on the allow-list."""

    def __init__(self, inner):
        self.inner = inner
        self.sent = []

    def write(self, frame):
        frame = bytes(frame)
        if not frame_allowed(frame):
            raise RadioError("refusing to send a frame that is not a read command: %s" % frame[:16].hex(" "))
        self.sent.append(frame)
        return self.inner.write(frame)

    def read(self, n):
        return self.inner.read(n)

    def reset_input_buffer(self):
        return self.inner.reset_input_buffer()

    def close(self):
        return self.inner.close()


class SerialTransport:
    def __init__(self, port, timeout=5.0):
        try:
            import serial
        except ImportError:
            raise RadioError("pyserial is missing: pip install pyserial")
        try:
            self.ser = serial.Serial(port, BAUD, timeout=timeout, write_timeout=timeout)
        except Exception as exc:
            raise RadioError("cannot open %s: %s" % (port, exc))

    def write(self, frame):
        self.ser.write(frame)
        self.ser.flush()

    def read(self, n):
        return self.ser.read(n)

    def reset_input_buffer(self):
        self.ser.reset_input_buffer()

    def close(self):
        # Closing the port is also how the radio is told to leave programming mode.
        try:
            self.ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------
# The radio
# ---------------------------------------------------------------------------------------------
class Radio:
    def __init__(self, transport, timing=None, log=None, sleep=time.sleep):
        self.tr = ReadOnlyGuard(transport)
        self.t = timing or Timing()
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        self.sleep = sleep
        self.vframes = {}
        self.model = ""

    # -- low level
    def _read_exact(self, n, what):
        data = b""
        deadline = time.time() + 15.0
        while len(data) < n:
            chunk = self.tr.read(n - len(data))
            if not chunk:
                raise RadioError("%s: no answer (got %d of %d bytes: %s)" % (what, len(data), n, data.hex(" ")))
            data += chunk
            if time.time() > deadline:
                raise RadioError("%s: too slow" % what)
        return data

    # -- connection
    def connect(self):
        t = self.t
        self.sleep(t.init)
        self.tr.reset_input_buffer()
        self.sleep(t.clear)
        self.sleep(t.clear)
        self.tr.write(b"PSEARCH")
        self.sleep(t.psearch)
        reply = self._read_exact(8, "PSEARCH")
        if reply[0] != 0x06:
            raise RadioError("PSEARCH: unexpected answer %s" % reply.hex(" "))
        self.model = reply[1:].decode("ascii", "ignore").replace("\0", "").strip()
        if not any(tag in self.model for tag in ("DP570", "DM32", "DM-32")):
            raise RadioError("this is not a DM-32UV: the radio says %r" % self.model)
        self.sleep(t.handshake)
        self.tr.write(b"PASSSTA")
        self.sleep(t.handshake)
        reply = self._read_exact(3, "PASSSTA")
        if reply[0] != 0x50:
            raise RadioError("PASSSTA: unexpected answer %s" % reply.hex(" "))
        self.sleep(t.handshake)
        self.tr.write(b"SYSINFO")
        self.sleep(t.handshake)
        if self._read_exact(1, "SYSINFO") != b"\x06":
            raise RadioError("SYSINFO was not acknowledged")
        self.sleep(0.010)

    def vframe(self, frame_id, hint=0x00):
        self.tr.write(bytes([0x56, 0x00, 0x00, hint, frame_id]))
        self.sleep(self.t.vframe)
        head = self._read_exact(3, "V-frame 0x%02X" % frame_id)
        if head[0] != 0x56 or head[1] != frame_id:
            raise RadioError("V-frame 0x%02X: unexpected header %s" % (frame_id, head.hex(" ")))
        payload = self._read_exact(head[2], "V-frame 0x%02X payload" % frame_id) if head[2] else b""
        self.sleep(self.t.vframe if head[2] else 0)
        return payload

    def query_vframes(self):
        for fid in VFRAME_IDS:
            try:
                self.vframes[fid] = self.vframe(fid)
            except RadioError as exc:
                self.log("note: %s" % exc)
        return self.vframes

    def enter_programming_mode(self):
        t = self.t
        self.tr.write(PROGRAM_FRAME)
        if self._read_exact(1, "PROGRAM") != b"\x06":
            raise RadioError("PROGRAM was refused")
        self.sleep(t.program)
        self.tr.write(b"\x02")
        if self._read_exact(8, "mode 02") != b"\xff" * 8:
            raise RadioError("mode 02 was refused")
        self.sleep(t.program)
        self.tr.write(b"\x06")
        if self._read_exact(1, "ack 06") != b"\x06":
            raise RadioError("programming mode was not acknowledged")
        self.sleep(t.program)

    def config_range(self):
        payload = self.vframes.get(0x0A) or self.vframe(0x0A)
        if len(payload) < 8:
            raise RadioError("the radio did not report its memory layout (V-frame 0x0A)")
        start, end = struct.unpack("<II", payload[:8])
        return start, end

    # -- memory
    def read_mem(self, addr, length):
        self.tr.write(b"\x52" + struct.pack("<I", addr)[:3] + struct.pack("<H", length))
        self.sleep(self.t.read_wait)
        head = self._read_exact(6, "read at 0x%06X" % addr)
        if head[0] != 0x57 or head[1:4] != struct.pack("<I", addr)[:3]:
            raise RadioError("read at 0x%06X: unexpected header %s" % (addr, head.hex(" ")))
        got = struct.unpack("<H", head[4:6])[0]
        if got != length:
            raise RadioError("read at 0x%06X: asked for %d bytes, the radio says %d" % (addr, length, got))
        data = self._read_exact(length, "read at 0x%06X payload" % addr)
        self.sleep(self.t.read_settle)
        return data

    def scan(self, progress=None):
        """Read the logical page number stored at +0xFFF of every 4 KB block. Returns {physical: id}."""
        start, end = self.config_range()
        last = (end // BLOCK) * BLOCK
        blocks = list(range(start, last + 1, BLOCK))
        found = {}
        for i, addr in enumerate(blocks):
            found[addr] = self.read_mem(addr + 0xFFF, 1)[0]
            self.sleep(self.t.probe_gap)
            if progress:
                progress("Scanning memory", i + 1, len(blocks))
        return found

    def read_pages(self, wanted, id_to_phys, progress=None):
        pages = {}
        for i, pid in enumerate(sorted(wanted)):
            phys = id_to_phys[pid]
            data = self.read_mem(phys, BLOCK)
            if data[0xFFF] != pid:
                raise RadioError("page 0x%02X: the block at 0x%06X now carries id 0x%02X; read again" % (pid, phys, data[0xFFF]))
            pages[pid] = data
            self.sleep(self.t.block_gap)
            if progress:
                progress("Reading pages", i + 1, len(wanted))
        return pages

    def close(self):
        self.tr.close()


def live_ids(scan):
    """{id: physical address}; blank blocks (0x00, 0xFF) are ignored; duplicates are an error."""
    out = {}
    for phys, pid in sorted(scan.items()):
        if pid in (0x00, 0xFF):
            continue
        if pid in out:
            raise RadioError("logical page 0x%02X appears twice (0x%06X and 0x%06X)" % (pid, out[pid], phys))
        out[pid] = phys
    return out


# ---------------------------------------------------------------------------------------------
# DfuSe output: the same container `dmrconf --bin read` writes (page id N lives at address N*0x1000)
# ---------------------------------------------------------------------------------------------
def dfuse_bytes(pages, name="Baofeng DM32UV Codeplug"):
    elements = b"".join(struct.pack("<II", pid * BLOCK, len(data)) + data for pid, data in sorted(pages.items()))
    target = b"Target" + bytes([1]) + struct.pack("<I", 1) + name.encode("ascii").ljust(255, b"\0")
    target += struct.pack("<II", len(elements), len(pages)) + elements
    # 11-byte file header; the image size field counts everything except the 16-byte suffix
    body = b"DfuSe" + bytes([1]) + struct.pack("<I", 11 + len(target)) + bytes([1]) + target
    suffix = b"\xff\xff\xff\xff\xff\xff" + struct.pack("<H", 0x011A) + b"UFD" + bytes([16])
    out = body + suffix
    return out + struct.pack("<I", (~zlib.crc32(out)) & 0xFFFFFFFF)


def parse_dfuse(blob):
    """Returns {page id: 4096 bytes} from a file made by this tool or by dmrconf."""
    if blob[:5] != b"DfuSe":
        raise ValueError("not a DfuSe file")
    stored = struct.unpack_from("<I", blob, len(blob) - 4)[0]
    if stored != (~zlib.crc32(blob[:-4])) & 0xFFFFFFFF:
        raise ValueError("bad checksum")
    pos = 11 + 11 + 255
    _, count = struct.unpack_from("<II", blob, pos)
    pos += 8
    pages = {}
    for _ in range(count):
        addr, size = struct.unpack_from("<II", blob, pos)
        pages[addr // BLOCK] = blob[pos + 8:pos + 8 + size]
        pos += 8 + size
    return pages


# ---------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------
def make_progress(quiet):
    state = {"last": None}

    def show(phase, i, n):
        if quiet:
            return
        pct = (100 * i) // n
        if (phase, pct) != state["last"]:
            state["last"] = (phase, pct)
            end = "\n" if i == n else "\r"
            print("%s: %3d%%" % (phase, pct), end=end, file=sys.stderr, flush=True)

    return show


def parse_ids(text):
    ids = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a, 0), int(b, 0) + 1))
        else:
            ids.add(int(part, 0))
    return ids


def find_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        raise RadioError("pyserial is missing: pip install pyserial")
    ports = [p for p in list_ports.comports() if (p.vid, p.pid) in ((0x1A86, 0x7523), (0x067B, 0x23A3))]
    if len(ports) == 1:
        return ports[0].device
    if not ports:
        raise RadioError("no programming cable found; give the port with --port (see: dm32_read.py ports)")
    raise RadioError("several cables found (%s); choose with --port" % ", ".join(p.device for p in ports))


def open_radio(args, transport=None, sleep=time.sleep):
    if transport is None:
        transport = SerialTransport(args.port or find_port())
    timing = Timing().scaled(args.delay_scale)
    return Radio(transport, timing, sleep=sleep)


def cmd_ports(args):
    try:
        from serial.tools import list_ports
    except ImportError:
        raise RadioError("pyserial is missing: pip install pyserial")
    ports = list(list_ports.comports())
    if not ports:
        print("no serial ports found")
    for p in ports:
        vid = "%04X" % p.vid if p.vid else "----"
        pid = "%04X" % p.pid if p.pid else "----"
        print("%-8s %s:%s  %s" % (p.device, vid, pid, p.description))


def describe_vframe(fid, payload):
    if fid in (0x06, 0x07, 0x08, 0x09, 0x0A, 0x0E, 0x0F) and len(payload) >= 8:
        a, b = struct.unpack("<II", payload[:8])
        return "0x%06X - 0x%06X" % (a, b)
    if fid == 0x10 and len(payload) >= 3:
        return str(int.from_bytes(payload[:3], "little"))
    if fid in (0x01, 0x03, 0x04, 0x05, 0x0B):
        return payload.decode("ascii", "ignore").replace("\0", "")
    return payload.hex(" ")


def cmd_info(args, transport=None, sleep=time.sleep):
    radio = open_radio(args, transport, sleep)
    try:
        radio.connect()
        radio.query_vframes()
        print("model: %s" % radio.model)
        for fid, payload in sorted(radio.vframes.items()):
            print("  0x%02X %-18s %s" % (fid, VFRAME_NAMES.get(fid, ""), describe_vframe(fid, payload)))
    finally:
        radio.close()


def run_scan(radio, args):
    radio.connect()
    radio.query_vframes()
    radio.enter_programming_mode()
    return radio.scan(make_progress(args.quiet))


def cmd_scan(args, transport=None, sleep=time.sleep):
    radio = open_radio(args, transport, sleep)
    try:
        scan = run_scan(radio, args)
    finally:
        radio.close()
    ids = live_ids(scan)
    doc = {
        "firmware": radio.vframes.get(0x01, b"").decode("ascii", "ignore").replace("\0", ""),
        "blocks": [{"physical": "0x%06X" % p, "id": "0x%02X" % i} for p, i in sorted(scan.items())],
        "ids": {"0x%02X" % i: "0x%06X" % p for i, p in sorted(ids.items())},
    }
    out = args.out or "dm32-scan-%s.json" % time.strftime("%Y%m%d-%H%M%S")
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=1)
    blank = len(scan) - len(ids)
    print("%d blocks scanned: %d hold a logical page, %d are blank. Saved %s" % (len(scan), len(ids), blank, out))


def cmd_read(args, transport=None, sleep=time.sleep):
    radio = open_radio(args, transport, sleep)
    try:
        scan = run_scan(radio, args)
        ids = live_ids(scan)
        if args.all:
            wanted = set(ids)
        elif args.ids:
            wanted = parse_ids(args.ids) & set(ids)
        else:
            wanted = {i for i in ids if DEFAULT_IDS[0] <= i <= DEFAULT_IDS[1]}
        if not wanted:
            raise RadioError("no pages to read")
        pages = radio.read_pages(wanted, ids, make_progress(args.quiet))
    finally:
        radio.close()
    out = args.out or "dm32-%s.bin" % time.strftime("%Y%m%d-%H%M%S")
    with open(out, "wb") as fh:
        fh.write(dfuse_bytes(pages))
    if args.raw_dir:
        import os
        os.makedirs(args.raw_dir, exist_ok=True)
        for pid, data in pages.items():
            with open(os.path.join(args.raw_dir, "page_%02X.bin" % pid), "wb") as fh:
                fh.write(data)
        with open(os.path.join(args.raw_dir, "scan.json"), "w") as fh:
            json.dump({"0x%06X" % p: "0x%02X" % i for p, i in sorted(scan.items())}, fh, indent=1)
    print("Saved %s: %d pages, %d bytes" % (out, len(pages), len(dfuse_bytes(pages))))
    return pages


def build_parser():
    p = argparse.ArgumentParser(description="Read-only reader for the Baofeng DM-32UV codeplug.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("ports", cmd_ports, "list serial ports"),
                            ("info", cmd_info, "print the radio's version information"),
                            ("scan", cmd_scan, "map memory blocks to logical pages (no data is read)"),
                            ("read", cmd_read, "read the codeplug into a .bin")):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        if name != "ports":
            s.add_argument("--port", help="serial port, e.g. COM3 (default: find the programming cable)")
            s.add_argument("--delay-scale", type=float, default=1.0,
                           help="multiply all delays (default 1.0; use 2.0 if the radio answers unreliably)")
            s.add_argument("--quiet", action="store_true", help="no progress output")
        if name in ("scan", "read"):
            s.add_argument("--out", help="output file")
        if name == "read":
            s.add_argument("--ids", help="logical pages to read, e.g. 0x03-0x67,0x70 (default: the range QDMR reads)")
            s.add_argument("--all", action="store_true",
                           help="read every page that exists, including ones no tool touches (experimental)")
            s.add_argument("--raw-dir", help="also save every page as a separate file and the block map")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except RadioError as exc:
        print("error: %s" % exc, file=sys.stderr)
        print("If the radio no longer answers, switch it off and on, and replug the cable.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
