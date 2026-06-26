#!/usr/bin/env python3
"""IC-9100 satmode UL (Sub band) frequency test.

Tests two approaches to setting the Sub band frequency in satmode:
  Approach A : set_freq(RIG_VFO_SUB_A, ul_hz)                    <- current code
  Approach B : set_vfo(RIG_VFO_MAIN) + set_freq(RIG_VFO_SUB_A)  <- proposed fix

Hamlib CI-V traffic is saved to /tmp/ic9100_ul_freq.log.
After each step the script prints what CI-V bytes were sent so you can
confirm whether 07 D1 (select Sub) was actually transmitted.

Usage (close FBSAT59 first — port must be free):
  source .venv/bin/activate
  python scripts/test_ic9100_ul_freq.py --port /dev/ttyUSB0 --civ 68

Arguments:
  --port   Serial port  (default: /dev/ttyUSB0)
  --baud   Baud rate    (default: 9600)
  --civ    CI-V address in hex without 0x (default: 68 for IC-9100)
  --model  Hamlib model (default: 3068 = IC-9100)
  --dl     DL frequency Hz (default: 435612000 = RS-44)
  --ul     UL frequency Hz (default: 145935000 = RS-44)
"""

import argparse
import os
import sys
import time

LOG_FILE = "/tmp/ic9100_ul_freq.log"

# ── Arguments ────────────────────────────────────────────────────────────────
p = argparse.ArgumentParser()
p.add_argument("--port", default="/dev/ttyUSB0")
p.add_argument("--baud", type=int, default=9600)
p.add_argument("--civ", default="68", help="CI-V hex (default: 68)")
p.add_argument("--model", type=int, default=3068, help="Hamlib model (default: 3068=IC-9100)")
p.add_argument("--dl", type=float, default=435612000.0)
p.add_argument("--ul", type=float, default=145935000.0)
args = p.parse_args()

civ_hex = f"0x{int(args.civ, 16):02X}"
ul = int(args.ul)
dl = int(args.dl)

# ── Redirect Hamlib debug (stderr) to log file ────────────────────────────────
log_fd = open(LOG_FILE, "w")
os.dup2(log_fd.fileno(), 2)  # stderr → log file from here on
os.set_inheritable(log_fd.fileno(), True)


def say(msg: str) -> None:
    """Print to original stdout (fd 1)."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def log_tail(keyword: str = "write") -> None:
    """Show recent CI-V lines from the Hamlib debug log."""
    log_fd.flush()
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
        hits = [l.rstrip() for l in lines if keyword.lower() in l.lower()]
        if hits:
            say(f"  [CI-V log — last {min(5, len(hits))} '{keyword}' lines]")
            for l in hits[-5:]:
                say(f"    {l}")
        else:
            say(f"  [CI-V log: no '{keyword}' lines found]")
    except Exception as e:
        say(f"  [log read error: {e}]")


def pause(prompt: str = "  >> Check IC-9100 Sub display. Press Enter to continue.") -> None:
    sys.stdout.write(prompt + " ")
    sys.stdout.flush()
    sys.stdin.readline()


# ── Hamlib setup ─────────────────────────────────────────────────────────────
try:
    import Hamlib
except ImportError:
    say("ERROR: Hamlib not found. Run: source .venv/bin/activate")
    sys.exit(1)

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_TRACE)  # all CI-V traffic → log file

rig = Hamlib.Rig(args.model)
rig.set_conf("rig_pathname", args.port)
rig.set_conf("serial_speed", str(args.baud))
rig.set_conf("civaddr", civ_hex)

say("=" * 60)
say(f"IC-9100 UL Freq Test")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}")
say(f"  DL={dl / 1e6:.6f} MHz   UL={ul / 1e6:.6f} MHz")
say(f"  Hamlib debug → {LOG_FILE}")
say("=" * 60)

say("\n[1] Opening rig...")
rig.open()
time.sleep(0.3)
say("    OK")

# ── Enable satmode ────────────────────────────────────────────────────────────
say("\n[2] Enable satmode (CI-V 16 59 01)...")
rc = rig.set_func(int(Hamlib.RIG_VFO_CURR), Hamlib.RIG_FUNC_SATMODE, 1)
say(f"    set_func(SATMODE, 1) rc={rc}")
time.sleep(0.3)

# ── Set DL on Main ────────────────────────────────────────────────────────────
say(f"\n[3] Set DL {dl / 1e6:.6f} MHz on MAIN...")
rc = rig.set_freq(int(Hamlib.RIG_VFO_MAIN), dl)
say(f"    set_freq(MAIN, {dl}) rc={rc}")
time.sleep(0.3)

# ─────────────────────────────────────────────────────────────────────────────
# Approach A: set_freq(SUB_A) directly  (= current app code)
# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "─" * 60)
say(f"Approach A: set_freq(RIG_VFO_SUB_A, {ul})  <- current code")
say("─" * 60)

# Clear the log so we only see new CI-V from this step
log_fd.seek(0)
log_fd.truncate()

rc = rig.set_freq(int(Hamlib.RIG_VFO_SUB_A), ul)
say(f"  set_freq(SUB_A, {ul}) rc={rc}")
time.sleep(0.3)

log_tail("write")  # show what was actually sent over CI-V
say(f"\n  Expected Sub: {ul / 1e6:.6f} MHz")
pause()

# ─────────────────────────────────────────────────────────────────────────────
# Approach B: set_vfo(MAIN) first, then set_freq(SUB_A)
# ─────────────────────────────────────────────────────────────────────────────
ul_b = ul + 1000  # +1 kHz so result is visually different from Approach A
say("\n" + "─" * 60)
say(f"Approach B: set_vfo(MAIN) → set_freq(RIG_VFO_SUB_A, {ul_b})")
say(f"  (+1 kHz offset = {ul_b / 1e6:.6f} MHz to distinguish from A)")
say("─" * 60)

log_fd.seek(0)
log_fd.truncate()

rc_vfo = rig.set_vfo(int(Hamlib.RIG_VFO_MAIN))
say(f"  set_vfo(MAIN) rc={rc_vfo}")
time.sleep(0.15)

rc_freq = rig.set_freq(int(Hamlib.RIG_VFO_SUB_A), ul_b)
say(f"  set_freq(SUB_A, {ul_b}) rc={rc_freq}")
time.sleep(0.3)

log_tail("write")
say(f"\n  Expected Sub: {ul_b / 1e6:.6f} MHz")
pause()

# ─────────────────────────────────────────────────────────────────────────────
# Approach C: set_split_freq(RIG_VFO_MAIN, ul_hz)  <- proposed fix
# ─────────────────────────────────────────────────────────────────────────────
ul_c = ul + 2000  # +2 kHz to distinguish from A and B
say("\n" + "─" * 60)
say(f"Approach C: set_split_freq(RIG_VFO_MAIN, {ul_c})")
say(f"  (+2 kHz offset = {ul_c / 1e6:.6f} MHz)")
say("─" * 60)

log_fd.seek(0)
log_fd.truncate()

rc_c = rig.set_split_freq(int(Hamlib.RIG_VFO_MAIN), ul_c)
say(f"  set_split_freq(MAIN, {ul_c}) rc={rc_c}")
time.sleep(0.3)

log_tail("write")
say(f"\n  Expected Sub: {ul_c / 1e6:.6f} MHz")
pause()

# ─────────────────────────────────────────────────────────────────────────────
# Approach D: set_func(SATMODE,1) + set_split_vfo(MAIN,1,MAIN) → set_split_freq(MAIN)
#
# set_func(SATMODE,1) puts the rig in satmode (CI-V 16 59 01) but leaves
# Hamlib rig->state.satmode=0.  Calling set_split_vfo(MAIN,1,MAIN) makes
# Hamlib query the rig satmode state (CI-V 16 59 fd); the rig answers
# satmode=1 so Hamlib sets state.satmode=1.  Then set_split_freq(MAIN)
# sees satmode=1 and routes the frequency to Sub band correctly.
# ─────────────────────────────────────────────────────────────────────────────
ul_d = ul + 3000  # +3 kHz to distinguish from A, B, C
say("\n" + "─" * 60)
say(f"Approach D: set_func(SATMODE,1) + set_split_vfo(MAIN,1,MAIN) → set_split_freq(MAIN, {ul_d})")
say(f"  (+3 kHz offset = {ul_d / 1e6:.6f} MHz)")
say("─" * 60)

log_fd.seek(0)
log_fd.truncate()

# Re-assert satmode ON (rig may already be in satmode, but ensure Hamlib sync)
rig.set_func(int(Hamlib.RIG_VFO_CURR), Hamlib.RIG_FUNC_SATMODE, 1)
time.sleep(0.2)
# Sync Hamlib internal state.satmode: Hamlib queries rig → rig replies satmode=1
rig.set_split_vfo(int(Hamlib.RIG_VFO_MAIN), 1, int(Hamlib.RIG_VFO_MAIN))
time.sleep(0.2)

rc_d = rig.set_split_freq(int(Hamlib.RIG_VFO_MAIN), ul_d)
say(f"  set_split_freq(MAIN, {ul_d}) rc={rc_d}")
time.sleep(0.3)

log_tail("write")
say(f"\n  Expected Sub: {ul_d / 1e6:.6f} MHz")
pause()

# ─────────────────────────────────────────────────────────────────────────────
say("\n[Done] Closing rig.")
rig.close()
log_fd.close()
say(f"Full Hamlib debug log saved to {LOG_FILE}")
say("  grep '07 d1\\|07 d0\\|write' /tmp/ic9100_ul_freq.log  <- CI-V Sub select")
