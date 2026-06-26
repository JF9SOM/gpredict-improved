#!/usr/bin/env python3
"""IC-9100 satmode UL frequency write diagnostic.

Reproduces the exact sequence the app uses (connect button flow), then
tries alternative approaches one by one with interactive pauses so you
can check the IC-9100 Sub display after each step.

Usage (FBSAT59 must be CLOSED — port must be free):
  source .venv/bin/activate
  python scripts/test_ic9100_satmode_ul.py

Options:
  --port   serial port   (default: /dev/ttyUSB0)
  --baud   baud rate     (default: 9600)
  --civ    CI-V hex      (default: 65 = IC-9100)
  --model  Hamlib model  (default: 3068 = IC-9100)
  --dl     DL freq Hz    (default: 437801000 = RS-44 approx)
  --ul     UL freq Hz    (default: 145990000 = RS-44 approx)
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_satmode_ul.log"

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

# Redirect Hamlib debug (stderr) to log file
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
    """Print last few CI-V write lines from Hamlib debug log."""
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        writes = [
            l.rstrip()
            for l in lines
            if "write" in l.lower() or "07 d" in l.lower() or "set_freq" in l.lower()
        ]
        for l in writes[-8:]:
            say(f"    {l}")
    except Exception as e:
        say(f"    [log error: {e}]")


def reset_log() -> None:
    _log_fd.seek(0)
    _log_fd.truncate()


# ── Load Hamlib ───────────────────────────────────────────────────────────────
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
VFO_B = int(H.RIG_VFO_B)
SATMODE = H.RIG_FUNC_SATMODE

rig = H.Rig(args.model)
rig.set_conf("rig_pathname", args.port)
rig.set_conf("serial_speed", str(args.baud))
rig.set_conf("civaddr", civ_hex)

say("=" * 65)
say("IC-9100 satmode UL frequency write test")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

say("\n[OPEN] Opening rig...")
rig.open()
time.sleep(0.5)
say("  OK")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("STEP 1 — Mimic _init_split: set_func(SATMODE, ON)")
say("=" * 65)
reset_log()
rc = rig.set_func(CURR, SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc}")
time.sleep(0.5)
show_civ()
pause("satmode ON via Hamlib")

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("STEP 2 — Mimic _satmode_enter (current app code):")
say("  set_func(SATMODE, OFF) → sleep 0.2 → set_freq(CURR, DL)")
say("  → set_freq(SUB_A, UL) → set_func(SATMODE, ON)")
say("=" * 65)
reset_log()

rc1 = rig.set_func(CURR, SATMODE, 0)
say(f"  set_func(SATMODE, 0) rc={rc1}")
time.sleep(0.2)
rc2 = rig.set_freq(CURR, DL)
say(f"  set_freq(CURR, {DL}) rc={rc2}")
rc3 = rig.set_freq(SUB_A, UL)
say(f"  set_freq(SUB_A, {UL}) rc={rc3}")
rc4 = rig.set_func(CURR, SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc4}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL / 1e6:.3f} MHz")
pause("after _satmode_enter sequence")

# ─────────────────────────────────────────────────────────────────────────────
UL2 = UL + 5_000
say("\n" + "=" * 65)
say(f"STEP 3 — Mimic periodic UL toggle (+5 kHz = {UL2 / 1e6:.3f} MHz):")
say("  set_func(SATMODE, OFF) → sleep 0.2 → set_freq(SUB_A) → SATMODE ON")
say("=" * 65)
reset_log()

rc1 = rig.set_func(CURR, SATMODE, 0)
say(f"  set_func(SATMODE, 0) rc={rc1}")
time.sleep(0.2)
rc2 = rig.set_freq(SUB_A, UL2)
say(f"  set_freq(SUB_A, {UL2}) rc={rc2}")
rc3 = rig.set_func(CURR, SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc3}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL2 / 1e6:.3f} MHz")
pause("periodic UL toggle")

# ─────────────────────────────────────────────────────────────────────────────
UL3 = UL + 10_000
say("\n" + "=" * 65)
say(f"STEP 4 — Longer sleep (0.5s) after SATMODE OFF (+10 kHz = {UL3 / 1e6:.3f} MHz)")
say("=" * 65)
reset_log()

rig.set_func(CURR, SATMODE, 0)
say("  set_func(SATMODE, 0)")
time.sleep(0.5)
rc = rig.set_freq(SUB_A, UL3)
say(f"  set_freq(SUB_A, {UL3}) rc={rc}")
rig.set_func(CURR, SATMODE, 1)
say("  set_func(SATMODE, 1)")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL3 / 1e6:.3f} MHz")
pause("0.5s sleep after SATMODE OFF")

# ─────────────────────────────────────────────────────────────────────────────
UL4 = UL + 15_000
say("\n" + "=" * 65)
say(f"STEP 5 — Try RIG_VFO_B instead of RIG_VFO_SUB_A (+15 kHz = {UL4 / 1e6:.3f} MHz)")
say("=" * 65)
reset_log()

rig.set_func(CURR, SATMODE, 0)
say("  set_func(SATMODE, 0)")
time.sleep(0.3)
rc = rig.set_freq(VFO_B, UL4)
say(f"  set_freq(VFO_B, {UL4}) rc={rc}")
rig.set_func(CURR, SATMODE, 1)
say("  set_func(SATMODE, 1)")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL4 / 1e6:.3f} MHz")
pause("RIG_VFO_B approach")

# ─────────────────────────────────────────────────────────────────────────────
UL5 = UL + 20_000
say("\n" + "=" * 65)
say(f"STEP 6 — set_split_freq(MAIN, ul) while satmode ON (+20 kHz = {UL5 / 1e6:.3f} MHz)")
say("  (no SATMODE toggle — rig stays in satmode)")
say("=" * 65)
reset_log()

rig.set_func(CURR, SATMODE, 1)
say("  set_func(SATMODE, 1)  [ensure ON]")
time.sleep(0.3)
rc = rig.set_split_freq(MAIN, UL5)
say(f"  set_split_freq(MAIN, {UL5}) rc={rc}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL5 / 1e6:.3f} MHz")
pause("set_split_freq while satmode ON")

# ─────────────────────────────────────────────────────────────────────────────
UL6 = UL + 25_000
say("\n" + "=" * 65)
say(
    f"STEP 7 — set_freq(SUB_A) DIRECTLY (no SATMODE toggle, satmode stays ON) (+25 kHz = {UL6 / 1e6:.3f} MHz)"
)
say("  ic9700_set_vfo will likely reject this — shows whether gate is the problem")
say("=" * 65)
reset_log()

rc = rig.set_freq(SUB_A, UL6)
say(f"  set_freq(SUB_A, {UL6}) rc={rc}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected Sub: {UL6 / 1e6:.3f} MHz  (probably rejected)")
pause("direct SUB_A write without SATMODE toggle")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[CLOSE] Done. Closing rig.")
rig.close()
_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
say("  To inspect CI-V bytes: grep -i '07.d\\|write\\|set_freq' " + LOG)
