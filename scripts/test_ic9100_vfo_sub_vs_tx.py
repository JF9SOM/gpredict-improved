#!/usr/bin/env python3
"""Compare RIG_VFO_TX vs RIG_VFO_SUB for UL frequency writes in SAT mode.

IC-9100 confirmed working with VFO_TX (test_ic9100_hamlib_satmode2.py).
IC-9700 friend reports TX freq not updating — suspect cache->satmode not set.
This script tests both VFO constants on IC-9100 to confirm they produce
identical CI-V commands and both update the Sub VFO display.

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_vfo_sub_vs_tx.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_vfo_sub_vs_tx.log"

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
UL0 = int(args.ul)

_log_fd = open(LOG, "w")
os.dup2(_log_fd.fileno(), 2)


def say(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def pause(label: str = "") -> None:
    sys.stdout.write(f"  >> {label}  [Enter] ")
    sys.stdout.flush()
    sys.stdin.readline()


def show_civ(n: int = 12) -> None:
    """Print recent CI-V related lines from the Hamlib debug log."""
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        keywords = (
            "write",
            "cmd",
            "5a",
            "set_func",
            "set_freq",
            "vfo_tx",
            "vfo_sub",
            "vfo_curr",
            "05 ",
            "07 ",
        )
        hits = [ln.rstrip() for ln in lines if any(k in ln.lower() for k in keywords)]
        for ln in hits[-n:]:
            say(f"    {ln}")
    except Exception as e:
        say(f"    [log error: {e}]")


def make_rig() -> "Hamlib.Rig":
    r = Hamlib.Rig(args.model)
    r.set_conf("rig_pathname", args.port)
    r.set_conf("serial_speed", str(args.baud))
    r.set_conf("civaddr", civ_hex)
    return r


try:
    import Hamlib
except ImportError:
    say("ERROR: Hamlib not found.  Run: source .venv/bin/activate")
    sys.exit(1)

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_TRACE)

VFO_CURR = int(Hamlib.RIG_VFO_CURR)
VFO_MAIN = int(Hamlib.RIG_VFO_MAIN)
VFO_TX = int(Hamlib.RIG_VFO_TX)
VFO_SUB = int(Hamlib.RIG_VFO_SUB)
SATMODE = Hamlib.RIG_FUNC_SATMODE

say("=" * 65)
say("VFO_TX vs VFO_SUB comparison test (SAT mode UL write)")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL base={UL0 / 1e6:.3f} MHz")
say(f"  VFO_TX=0x{VFO_TX:08X}  VFO_SUB=0x{VFO_SUB:08X}")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 1 — open() → set_func(SATMODE,1) → close() → open()")
say("  (Establishes cache->satmode=1 for VFO_TX routing)")
say("=" * 65)

rig = make_rig()
rig.open()
time.sleep(0.3)
say("  rig.open() [1st] OK")
rc_sat = rig.set_func(SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc_sat}  (0=OK)")
time.sleep(0.4)
show_civ(6)
rig.close()
time.sleep(0.3)
say("  rig.close() OK")

rig2 = make_rig()
rig2.open()
time.sleep(0.3)
say("  rig2.open() [2nd] OK  → Hamlib reads satmode=1 → cache->satmode=1")
show_civ(4)
pause(f"SAT lamp ON? rc_sat={rc_sat}")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 2 — set DL frequency on Main (baseline)")
say("=" * 65)

rc_dl = rig2.set_freq(VFO_MAIN, DL)
say(f"  set_freq(VFO_MAIN, {DL}) rc={rc_dl}")
time.sleep(0.3)
show_civ(4)
pause(f"Main={DL / 1e6:.3f} MHz displayed?  rc={rc_dl}")

# ─────────────────────────────────────────────────────────────────────────────
UL_A = UL0
say(f"\nSTEP 3 — RIG_VFO_TX: set_freq(VFO_TX, {UL_A / 1e6:.3f} MHz)")
say("=" * 65)

rc_tx1 = rig2.set_freq(VFO_TX, UL_A)
say(f"  set_freq(VFO_TX, {UL_A}) rc={rc_tx1}  (0=OK, non-zero=error)")
time.sleep(0.4)
show_civ(8)
pause(f"Sub={UL_A / 1e6:.3f} MHz displayed?  rc={rc_tx1}")

# ─────────────────────────────────────────────────────────────────────────────
UL_B = UL0 + 5_000
say(f"\nSTEP 4 — RIG_VFO_TX again: set_freq(VFO_TX, {UL_B / 1e6:.3f} MHz)  (+5 kHz)")
say("=" * 65)

rc_tx2 = rig2.set_freq(VFO_TX, UL_B)
say(f"  set_freq(VFO_TX, {UL_B}) rc={rc_tx2}")
time.sleep(0.4)
show_civ(6)
pause(f"Sub changed to {UL_B / 1e6:.3f} MHz?  rc={rc_tx2}")

# ─────────────────────────────────────────────────────────────────────────────
UL_C = UL0 + 10_000
say(f"\nSTEP 5 — RIG_VFO_SUB: set_freq(VFO_SUB, {UL_C / 1e6:.3f} MHz)  (+10 kHz)")
say("  (Does NOT rely on cache->satmode — directly addresses Sub)")
say("=" * 65)

rc_sub1 = rig2.set_freq(VFO_SUB, UL_C)
say(f"  set_freq(VFO_SUB, {UL_C}) rc={rc_sub1}  (0=OK, non-zero=error)")
time.sleep(0.4)
show_civ(8)
pause(f"Sub changed to {UL_C / 1e6:.3f} MHz?  rc={rc_sub1}")

# ─────────────────────────────────────────────────────────────────────────────
UL_D = UL0 + 15_000
say(f"\nSTEP 6 — RIG_VFO_SUB again: set_freq(VFO_SUB, {UL_D / 1e6:.3f} MHz)  (+15 kHz)")
say("=" * 65)

rc_sub2 = rig2.set_freq(VFO_SUB, UL_D)
say(f"  set_freq(VFO_SUB, {UL_D}) rc={rc_sub2}")
time.sleep(0.4)
show_civ(6)
pause(f"Sub changed to {UL_D / 1e6:.3f} MHz?  rc={rc_sub2}")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("[SUMMARY]")
say(f"  STEP 1  set_func(SATMODE,1):          rc={rc_sat}")
say(f"  STEP 2  set_freq(VFO_MAIN, DL):       rc={rc_dl}")
say(f"  STEP 3  set_freq(VFO_TX,  UL+0):      rc={rc_tx1}")
say(f"  STEP 4  set_freq(VFO_TX,  UL+5k):     rc={rc_tx2}")
say(f"  STEP 5  set_freq(VFO_SUB, UL+10k):    rc={rc_sub1}")
say(f"  STEP 6  set_freq(VFO_SUB, UL+15k):    rc={rc_sub2}")
say("")
say("  判定基準:")
say("  VFO_TX rc=0 かつ Sub が変化した  → cache->satmode OK、VFO_TX で動作")
say("  VFO_TX rc=0 だが Sub が変化しない→ cache->satmode 未設定、VFO_SUB に切替が必要")
say("  VFO_SUB rc=0 かつ Sub が変化した → VFO_SUB は cache 非依存で動作 ✓")
say("  VFO_SUB rc≠0                     → SAT モード外では VFO_SUB 自体が無効")
say("=" * 65)

rig2.close()
_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
