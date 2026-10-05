# dm32_tools: read the DM-32UV and map its settings, without writing

| File | What it is |
|---|---|
| `dm32_read.py` | Read-only reader. Writes a `.bin` in the same format as `dmrconf --bin read`. |
| `mapbatch.py` | Logs "I changed X" steps and reports which bytes moved. |
| `whatchanged.py` | Compares two `.bin` files (used by `mapbatch.py`). |
| `test_dm32_read.py` | Offline tests: no radio and no pyserial needed. |
| `MAPPING_PLAN.md` | The step-by-step plan for the mapping work. |

## Quick start

```
pip install pyserial
python dm32_read.py ports
python dm32_read.py info --port COM3
python dm32_read.py read --port COM3 --out my.bin        # about 1 minute
```
`--port` can be left out if only one programming cable is plugged in.
If the radio answers unreliably, add `--delay-scale 2` (doubles every wait).

## Safety

The program cannot write to the radio. The only things it ever sends are the handshake, version
queries, the "programming mode" entry that every tool needs before reading, and memory reads. A guard
in the code rejects any other frame before it reaches the port, and the test file checks this.
Closing the port at the end takes the radio out of programming mode.

## What was tested here, and what was not

- Against a **simulated radio** holding your `dm32.bin`: the file it writes is byte-for-byte identical to
  your dump, it probes all 200 memory blocks, and reads each page once.
- Against the **real recorded traffic of the official CPS** (the `serial_capture_example.txt` file in the
  DM32-Protocol-Spec repository, firmware DM32.01.01.046): every frame the client sends is a frame the
  official CPS also sent, and the pages it returns are the bytes the real radio returned.
- **Not tested:** a real radio and a real serial port. The delays come from the spec and NeonPlug.
  Do the first-run comparison in `MAPPING_PLAN.md` section 0 before you rely on it. The `--all` option
  has not been tried on hardware.

Run the tests:
```
python test_dm32_read.py --image my.bin --capture serial_capture_example.txt
```

## Notes

- A read takes about a minute: about 12 s for the memory-block scan, then about 0.5 s per 4 KB page.
  The speed is limited by the radio's 115,200 baud link, so this is not much faster than QDMR.
- The memory-block scan is part of the official CPS's own connection sequence too.
- Page `id` N is stored at address N x 0x1000 in the output file, the same as QDMR's files.
