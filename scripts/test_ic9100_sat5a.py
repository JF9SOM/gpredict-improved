#!/usr/bin/env python3
"""IC-9100 satmode test using correct CI-V 16 5A command.

Previous tests used 16 59 (dual watch) instead of 16 5A (satellite mode).
This script tests:
  STEP 1: pyserial 16 5A 01 → SAT lamp should light
  STEP 2: Hamlib open (cache.satmode stays 0) → set_freq(VFO_TX) test
  STEP 3: set_freq(SUB_A) test (no ic9700_set_vfo rejection if cache.satmode=0)
  STEP 4: toggle 16 5A OFF→SUB_A→ON (current app method but with correct cmd)

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_sat5a.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_ic9100_sat5a.log"

p = argparse.ArgumentParser()
p.add_argument("--port", default="/dev/ttyUSB0")
p.add_argument("--baud", type=int, default=9600)
p.add_argument("--civ", default="65")
p.add_argument("--model", type=int, default=3068)
p.add_argument("--dl", type=float, default=437_801_000.0)
p.add_argument("--ul", type=float, default=145_990_000.0)
args = p.parse_args()

civ_addr = int(args.civ, 16)
DL = int(args.dl)
UL = int(args.ul)

_log_fd = open(LOG, "w")
os.dup2(_log_fd.fileno(), 2)


def say(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def pause(label: str = "") -> None:
    msg = f"  >> {label}  Press Enter to continue. "
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stdin.readline()


# ── CI-V helpers ─────────────────────────────────────────────────────────────


def civ_frame(cmd: int, subcmd: int, data: list[int] | None = None) -> bytes:
    body = [0xFE, 0xFE, civ_addr, 0xE0, cmd, subcmd]
    if data:
        body.extend(data)
    body.append(0xFD)
    return bytes(body)


def sat_on_frame() -> bytes:
    return civ_frame(0x16, 0x5A, [0x01])


def sat_off_frame() -> bytes:
    return civ_frame(0x16, 0x5A, [0x00])


def pyserial_write(ser, frame: bytes, label: str) -> None:
    hex_str = " ".join(f"{b:02X}" for b in frame)
    say(f"    pyserial TX: {hex_str}  [{label}]")
    ser.write(frame)
    time.sleep(0.15)
    rx = ser.read(64)
    if rx:
        say(f"    pyserial RX: {' '.join(f'{b:02X}' for b in rx)}")


# ─────────────────────────────────────────────────────────────────────────────
say("=" * 65)
say("IC-9100 satmode test — correct CI-V 16 5A")
say(f"  port={args.port}  baud={args.baud}  civ=0x{civ_addr:02X}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

try:
    import serial as _serial
except ImportError:
    say("ERROR: pyserial not found. pip install pyserial")
    sys.exit(1)

try:
    import Hamlib
except ImportError:
    say("ERROR: Hamlib not found. source .venv/bin/activate")
    sys.exit(1)

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_TRACE)
H = Hamlib
CURR = int(H.RIG_VFO_CURR)
SUB_A = int(H.RIG_VFO_SUB_A)
VFO_TX = int(H.RIG_VFO_TX)

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("STEP 1 — pyserial 16 5A 01 (satmode ON)  [Hamlib NOT open]")
say("=" * 65)
ser = _serial.Serial(args.port, args.baud, timeout=0.5)
time.sleep(0.1)
pyserial_write(ser, sat_on_frame(), "SATMODE ON via 16 5A 01")
ser.close()
say("  pyserial closed.")
pause("SAT lamp should be ON. Check IC-9100 display.")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("STEP 2 — Hamlib open (cache.satmode stays 0)")
say("         set_freq(CURR, DL) then set_freq(VFO_TX, UL)")
say("=" * 65)
rig = H.Rig(args.model)
rig.set_conf("rig_pathname", args.port)
rig.set_conf("serial_speed", str(args.baud))
rig.set_conf("civaddr", f"0x{civ_addr:02X}")
rig.open()
time.sleep(0.3)
say("  Hamlib open OK")

rc1 = rig.set_freq(CURR, DL)
say(f"  set_freq(CURR, {DL}) rc={rc1}  [DL → Main]")
time.sleep(0.2)
rc2 = rig.set_freq(VFO_TX, UL)
say(f"  set_freq(VFO_TX, {UL}) rc={rc2}  [UL → VFO_TX]")
time.sleep(0.5)
say(f"\n  Expected: Main={DL / 1e6:.3f} MHz  Sub={UL / 1e6:.3f} MHz")
pause("VFO_TX result. SAT lamp on? Sub changed?")

# ─────────────────────────────────────────────────────────────────────────────
UL2 = UL + 5_000
say("\n" + "=" * 65)
say(f"STEP 3 — set_freq(SUB_A, UL+5kHz) — Hamlib still open")
say(f"  (ic9700_set_vfo should NOT reject SUB_A if cache.satmode=0)")
say("=" * 65)
rc3 = rig.set_freq(SUB_A, UL2)
say(f"  set_freq(SUB_A, {UL2}) rc={rc3}  [UL+5k → SUB_A]")
time.sleep(0.5)
say(f"\n  Expected: Sub={UL2 / 1e6:.3f} MHz")
pause("SUB_A result. Sub changed?")

# ─────────────────────────────────────────────────────────────────────────────
UL3 = UL + 10_000
say("\n" + "=" * 65)
say(f"STEP 4 — toggle: pyserial 16 5A OFF → set_freq(SUB_A) → 16 5A ON")
say(f"  (correct toggle using 16 5A, with Hamlib open)")
say("=" * 65)
ser2 = _serial.Serial(args.port, args.baud, timeout=0.5)
pyserial_write(ser2, sat_off_frame(), "SATMODE OFF via 16 5A 00")
time.sleep(0.2)
rc4 = rig.set_freq(SUB_A, UL3)
say(f"  set_freq(SUB_A, {UL3}) rc={rc4}  [UL+10k → SUB_A]")
time.sleep(0.1)
pyserial_write(ser2, sat_on_frame(), "SATMODE ON via 16 5A 01")
ser2.close()
time.sleep(0.5)
say(f"\n  Expected: Sub={UL3 / 1e6:.3f} MHz")
pause("toggle+SUB_A result (correct 16 5A). Sub changed? SAT lamp?")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[CLOSE] Done.")
rig.close()
_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
