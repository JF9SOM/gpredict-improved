#!/usr/bin/env python3
"""IC-9100 satmode test using Hamlib set_func(SATMODE) only — no pyserial.

Goal: verify whether set_func(RIG_FUNC_SATMODE, 1) works correctly when
called AFTER setting DL/UL frequencies (Main=UHF, Sub=VHF on different
bands), without any pyserial pre-open CI-V injection.

If this works for IC-9100, the same code path will also work for IC-910H
(model 3044) because Hamlib uses 1A 07 for that model automatically.

Test sequence:
  STEP 1 — rig.open()  (Hamlib init, cache.satmode=0, VFO=MAIN)
  STEP 2 — set_freq(MAIN, dl)   Main band → UHF (435 MHz)
            set_freq(SUB_A, ul) Sub band  → VHF (145 MHz)
            [now Main and Sub are on different bands]
  STEP 3 — set_func(SATMODE, 1)  ask IC-9100 to enter SAT mode
            → expected: SAT lamp lights, satmode confirmed
  STEP 4 — set_freq(VFO_TX, ul+5k)  periodic UL Doppler update
            → expected: Sub changes, SAT lamp stays on
  STEP 5 — set_freq(CURR, dl+1k)  periodic DL Doppler update
            → expected: Main changes, SAT lamp stays on

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_hamlib_satmode.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_ic9100_hamlib_satmode.log"

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


def show_civ(n: int = 12) -> None:
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        hits = [
            l.rstrip()
            for l in lines
            if any(k in l.lower() for k in ("write", "cmd", "set_freq", "set_func", "07 d", "5a"))
        ]
        for l in hits[-n:]:
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
say("IC-9100 Hamlib-only satmode test  (no pyserial)")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL={UL / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

rig.open()
time.sleep(0.3)
say("  rig.open() OK")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 1 — set_freq(MAIN, DL) then set_freq(SUB_A, UL)")
say(f"  DL={DL / 1e6:.3f} MHz (UHF)   UL={UL / 1e6:.3f} MHz (VHF)")
say("  [cache.satmode=0 so SUB_A should not be rejected]")
say("=" * 65)
reset_log()
rc1 = rig.set_freq(MAIN, DL)
say(f"  set_freq(MAIN, {DL}) rc={rc1}")
time.sleep(0.2)
rc2 = rig.set_freq(SUB_A, UL)
say(f"  set_freq(SUB_A, {UL}) rc={rc2}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected: Main={DL / 1e6:.3f} MHz  Sub={UL / 1e6:.3f} MHz")
pause("Did Sub change to UL freq? (rc=0 means success)")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 2 — set_func(SATMODE, 1)  [Main=UHF, Sub=VHF → different bands]")
say("  Hamlib will send CI-V 16 5A 01 for IC-9100 (1A 07 for IC-910H)")
say("=" * 65)
reset_log()
rc3 = rig.set_func(CURR, SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc3}")
time.sleep(0.5)
show_civ()
pause("SAT lamp ON? Did IC-9100 accept satmode?  [KEY RESULT]")

# ─────────────────────────────────────────────────────────────────────────────
UL2 = UL + 5_000
say("\nSTEP 3 — set_freq(VFO_TX, UL+5k)  [periodic UL Doppler update]")
say(f"  UL+5kHz = {UL2 / 1e6:.3f} MHz")
say("=" * 65)
reset_log()
rc4 = rig.set_freq(VFO_TX, UL2)
say(f"  set_freq(VFO_TX, {UL2}) rc={rc4}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected: Sub={UL2 / 1e6:.3f} MHz  SAT lamp still ON")
pause("Sub changed? SAT lamp still on?")

# ─────────────────────────────────────────────────────────────────────────────
DL2 = DL + 1_000
say("\nSTEP 4 — set_freq(CURR, DL+1k)  [periodic DL Doppler update]")
say(f"  DL+1kHz = {DL2 / 1e6:.3f} MHz")
say("=" * 65)
reset_log()
rc5 = rig.set_freq(CURR, DL2)
say(f"  set_freq(CURR, {DL2}) rc={rc5}")
time.sleep(0.5)
show_civ()
say(f"\n  Expected: Main={DL2 / 1e6:.3f} MHz  SAT lamp still ON")
pause("Main changed? SAT lamp still on?")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[SUMMARY]")
say(f"  STEP 1 set_freq MAIN:    rc={rc1}  Sub: rc={rc2}")
say(f"  STEP 2 set_func SATMODE: rc={rc3}  ← KEY: 0=success, -11=failed")
say(f"  STEP 3 VFO_TX UL:        rc={rc4}")
say(f"  STEP 4 CURR DL:          rc={rc5}")
say("")
say("[CLOSE] Done.")
rig.close()
_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
