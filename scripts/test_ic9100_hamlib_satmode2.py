#!/usr/bin/env python3
"""IC-9100 satmode test: Hamlib set_func(SATMODE) immediately after open, no freq writes.

Previous test (test_ic9100_hamlib_satmode.py) called set_freq(MAIN/SUB_A) before
set_func(SATMODE). Those writes may have disturbed IC-9100 internal state.

This test calls set_func(SATMODE, 1) IMMEDIATELY after rig.open() with no writes first.
If this works, we can replace pyserial with:
  1. rig.open()
  2. set_func(SATMODE, 1)   -- Hamlib sends correct CI-V per model (16 5A / 1A 07)
  3. rig.close()
  4. rig.open()             -- reads satmode=1, cache->satmode=1 established
  5. normal Doppler operation

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_hamlib_satmode2.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_ic9100_hamlib_satmode2.log"

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
    msg = f"  >> {label}  Press Enter to continue. "
    sys.stdout.write(msg)
    sys.stdout.flush()
    sys.stdin.readline()


def show_civ(n: int = 10) -> None:
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        hits = [
            l.rstrip()
            for l in lines
            if any(k in l.lower() for k in ("write", "cmd", "5a", "1a 07", "set_func", "set_freq"))
        ]
        for l in hits[-n:]:
            say(f"    {l}")
    except Exception as e:
        say(f"    [log error: {e}]")


def make_rig() -> "Hamlib.Rig":
    rig = Hamlib.Rig(args.model)
    rig.set_conf("rig_pathname", args.port)
    rig.set_conf("serial_speed", str(args.baud))
    rig.set_conf("civaddr", civ_hex)
    return rig


try:
    import Hamlib
except ImportError:
    say("ERROR: Hamlib not found. Run: source .venv/bin/activate")
    sys.exit(1)

Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_TRACE)

H = Hamlib
CURR = int(H.RIG_VFO_CURR)
VFO_TX = int(H.RIG_VFO_TX)
SATMODE = H.RIG_FUNC_SATMODE

say("=" * 65)
say("IC-9100 Hamlib-only satmode2 test")
say("  Sequence: open → set_func(SATMODE,1) immediately → close → open")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 1 — first open() then IMMEDIATELY set_func(SATMODE, 1), no freq writes")
say("=" * 65)
rig = make_rig()
rig.open()
time.sleep(0.3)
say("  rig.open() OK")

rc_sat = rig.set_func(SATMODE, 1)
say(f"  set_func(SATMODE, 1) [2-arg, no VFO] rc={rc_sat}")
time.sleep(0.5)
show_civ()
say("  closing...")
rig.close()
time.sleep(0.3)
pause(f"SAT lamp ON? (rc={rc_sat}, 0=success)")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 2 — second open() to establish cache->satmode=1")
say("=" * 65)
rig2 = make_rig()
rig2.open()
time.sleep(0.3)
say("  rig2.open() OK  (Hamlib should read satmode=1 → cache established)")
show_civ()
pause("SAT lamp still ON after second open?")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 3 — set_freq(CURR, DL) then set_freq(VFO_TX, UL)")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say("=" * 65)
rc1 = rig2.set_freq(CURR, DL)
say(f"  set_freq(CURR, {DL}) rc={rc1}  [Main=DL]")
time.sleep(0.2)
rc2 = rig2.set_freq(VFO_TX, UL)
say(f"  set_freq(VFO_TX, {UL}) rc={rc2}  [Sub=UL]")
time.sleep(0.5)
show_civ()
say(f"\n  Expected: Main={DL / 1e6:.3f} MHz  Sub={UL / 1e6:.3f} MHz  SAT lamp ON")
pause("Main/Sub correct? SAT lamp on?")

# ─────────────────────────────────────────────────────────────────────────────
UL2 = UL + 5_000
say(f"\nSTEP 4 — periodic UL update: set_freq(VFO_TX, UL+5k={UL2 / 1e6:.3f} MHz)")
say("=" * 65)
rc3 = rig2.set_freq(VFO_TX, UL2)
say(f"  set_freq(VFO_TX, {UL2}) rc={rc3}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected: Sub={UL2 / 1e6:.3f} MHz  SAT lamp still ON")
pause("Sub changed? SAT lamp still on?")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[SUMMARY]")
say(f"  STEP 1 set_func(SATMODE,1): rc={rc_sat}  ← None/0=success, non-zero=failed")
say(f"  STEP 3 set_freq(CURR/DL):   rc={rc1}")
say(f"         set_freq(VFO_TX/UL): rc={rc2}")
say(f"  STEP 4 set_freq(VFO_TX/UL+5k): rc={rc3}")
say("")
say("  If STEP 1 rc=0 and SAT lamp lit: pyserial can be removed entirely.")
say("  Hamlib will use correct CI-V per model (16 5A for 9100/9700, 1A 07 for 910H).")

say("\n[CLOSE]")
rig2.close()
_log_fd.close()
say(f"Full Hamlib debug log: {LOG}")
