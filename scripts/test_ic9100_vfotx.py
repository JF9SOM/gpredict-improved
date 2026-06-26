#!/usr/bin/env python3
"""IC-9100 satmode UL write test: RIG_VFO_TX vs toggle approach.

SAFE VERSION: open/close only once to avoid baud rate corruption.
Warmup is done via a long sleep after SATMODE ON within the same connection.

Run with FBSAT59 CLOSED (port must be free):
  source .venv/bin/activate
  python scripts/test_ic9100_vfotx.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_ic9100_vfotx.log"

p = argparse.ArgumentParser()
p.add_argument("--port", default="/dev/ttyUSB0")
p.add_argument("--baud", type=int, default=9600)
p.add_argument("--civ", default="65")
p.add_argument("--model", type=int, default=3068)
p.add_argument("--dl", type=float, default=437_801_000.0)
p.add_argument("--ul", type=float, default=145_990_000.0)
args = p.parse_args()

civ_hex = f"0x{int(args.civ, 16):02X}"
DL = int(args.dl)
UL = int(args.ul)

_log_fd = open(LOG, "w")
os.dup2(_log_fd.fileno(), 2)


def say(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def pause(label: str = "") -> None:
    msg = (
        f"  >> Check IC-9100 Sub display{': ' + label if label else ''}. Press Enter to continue. "
    )
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stdin.readline()


def show_civ() -> None:
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        hits = [
            l.rstrip()
            for l in lines
            if "write" in l.lower() or "07 d" in l.lower() or "set_freq" in l.lower()
        ]
        for l in hits[-8:]:
            say(f"    {l}")
    except Exception as e:
        say(f"    [log error: {e}]")


def reset_log() -> None:
    _log_fd.seek(0)
    _log_fd.truncate()


try:
    import Hamlib
except ImportError:
    say("ERROR: Hamlib not found. Run: source .venv/bin/activate")
    sys.exit(1)

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_TRACE)

H = Hamlib
CURR = int(H.RIG_VFO_CURR)
MAIN = int(H.RIG_VFO_MAIN)
SUB_A = int(H.RIG_VFO_SUB_A)
VFO_TX = int(H.RIG_VFO_TX)
SATMODE = H.RIG_FUNC_SATMODE

rig = H.Rig(args.model)
rig.set_conf("rig_pathname", args.port)
rig.set_conf("serial_speed", str(args.baud))
rig.set_conf("civaddr", civ_hex)

say("=" * 65)
say("IC-9100 UL write test: RIG_VFO_TX vs toggle  [SAFE: 1 open/close]")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

say("\n[OPEN] Opening rig (single open — no close until end)...")
rig.open()
time.sleep(0.3)
say("  OK")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("STEP 1 — SATMODE ON + 2.5s wait  (warmup within same connection)")
say("  Equivalent to app warmup(1.5s) + _init_split sleep(1.0s)")
say("=" * 65)
reset_log()
rc = rig.set_func(CURR, SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc}")
say("  Waiting 2.5 s for IC-9100 dual-band routing to initialize...")
time.sleep(2.5)
show_civ()
pause("SATMODE ON + 2.5s — IC-9100 should show SAT indicator")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say(f"STEP 2 — set_freq(CURR, DL) then set_freq(VFO_TX, UL) — no toggle")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say("=" * 65)
reset_log()
rc1 = rig.set_freq(CURR, DL)
say(f"  set_freq(CURR, {DL}) rc={rc1}  [DL to MAIN]")
time.sleep(0.2)
rc2 = rig.set_freq(VFO_TX, UL)
say(f"  set_freq(VFO_TX, {UL}) rc={rc2}  [UL to Sub]")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Main: {DL / 1e6:.3f} MHz  Sub: {UL / 1e6:.3f} MHz")
pause("DL→CURR then UL→VFO_TX")

# ─────────────────────────────────────────────────────────────────────────────
UL2 = UL + 5_000
say("\n" + "=" * 65)
say(f"STEP 3 — set_freq(VFO_TX, UL+5kHz) alone — no DL write, satmode ON")
say(f"  UL+5kHz = {UL2 / 1e6:.3f} MHz")
say("=" * 65)
reset_log()
rc = rig.set_freq(VFO_TX, UL2)
say(f"  set_freq(VFO_TX, {UL2}) rc={rc}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL2 / 1e6:.3f} MHz")
pause("VFO_TX alone (no DL)")

# ─────────────────────────────────────────────────────────────────────────────
UL3 = UL + 10_000
say("\n" + "=" * 65)
say(f"STEP 4 — Toggle (SATMODE OFF→ON) + set_freq(SUB_A) — current app method")
say(f"  UL+10kHz = {UL3 / 1e6:.3f} MHz  (for comparison)")
say("=" * 65)
reset_log()
rig.set_func(CURR, SATMODE, 0)
say("  set_func(SATMODE, 0)")
time.sleep(0.2)
rc = rig.set_freq(SUB_A, UL3)
say(f"  set_freq(SUB_A, {UL3}) rc={rc}")
rig.set_func(CURR, SATMODE, 1)
say("  set_func(SATMODE, 1)")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL3 / 1e6:.3f} MHz")
pause("toggle+SUB_A (current method)")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[CLOSE] Done. Closing rig (single close).")
rig.close()
_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
