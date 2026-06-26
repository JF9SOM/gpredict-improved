#!/usr/bin/env python3
"""IC-9100 Hamlib mode + CTCSS test (satmode).

STEP 1: satmode ON (open -> set_func -> close -> open)
STEP 2: Main=FM, Sub=FM
STEP 3: CTCSS 67.0 Hz ON — just tone commands, no mode change
STEP 4: CTCSS OFF

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_mode_hamlib.py --port /dev/ttyUSB0 --civ 65
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_ic9100_mode_hamlib.log"

p = argparse.ArgumentParser()
p.add_argument("--port", default="/dev/ttyUSB0")
p.add_argument("--baud", type=int, default=9600)
p.add_argument("--civ", default="65")
p.add_argument("--model", type=int, default=3068)
args = p.parse_args()

civ_hex = f"0x{int(args.civ, 16):02X}"

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


def show_civ(n: int = 15) -> None:
    _log_fd.flush()
    try:
        with open(LOG) as f:
            lines = f.readlines()
        hits = [
            l.rstrip()
            for l in lines
            if any(
                k in l.lower()
                for k in (
                    "write",
                    "cmd",
                    "set_mode",
                    "vfo",
                    "16 42",
                    "1b 00",
                    "07 d",
                    "tone",
                    "ctcss",
                    "func",
                )
            )
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
SATMODE = H.RIG_FUNC_SATMODE
FUNC_TONE = H.RIG_FUNC_TONE
VFO_MAIN = int(H.RIG_VFO_MAIN)
VFO_SUB = int(H.RIG_VFO_SUB)
MODE_FM = int(H.RIG_MODE_FM)
MODE_USB = int(H.RIG_MODE_USB)

say("=" * 65)
say(f"port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"VFO_MAIN=0x{VFO_MAIN:08X}  VFO_SUB=0x{VFO_SUB:08X}")
say(f"Hamlib debug -> {LOG}")
say("=" * 65)

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 1 — satmode ON: open -> set_func(SATMODE,1) -> close -> open")
say("=" * 65)
rig = make_rig()
rig.open()
time.sleep(0.3)
rc = rig.set_func(SATMODE, 1)
say(f"  set_func(SATMODE, 1) rc={rc}")
time.sleep(0.3)
rig.close()
time.sleep(0.3)

rig2 = make_rig()
rig2.open()
time.sleep(0.3)
say("  second open() done")
pause("SAT lamp ON?")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 2 — Main=FM, Sub=FM")
say("=" * 65)
rc2a = rig2.set_mode(MODE_FM, 0, VFO_MAIN)
rc2b = rig2.set_mode(MODE_FM, 0, VFO_SUB)
say(f"  set_mode(FM, VFO_MAIN) rc={rc2a}   set_mode(FM, VFO_SUB) rc={rc2b}")
time.sleep(0.5)
show_civ(8)
pause("Main=FM, Sub=FM?")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 3 — CTCSS 67.0 Hz ON on Sub")
say("  set_vfo(SUB) -> set_ctcss_tone(VFO_SUB, 670) -> set_func(TONE, 1)")
say("  -> set_vfo(MAIN) -> set_func(TONE, 0) on Main")
say("=" * 65)
TONE_DECI = 670  # 67.0 Hz in deciHz

rc3a = rig2.set_vfo(VFO_SUB)
say(f"  set_vfo(VFO_SUB) rc={rc3a}")
time.sleep(0.2)
rc3b = rig2.set_ctcss_tone(VFO_SUB, TONE_DECI)
say(f"  set_ctcss_tone(VFO_SUB, {TONE_DECI}) rc={rc3b}")
time.sleep(0.2)
rc3c = rig2.set_func(FUNC_TONE, 1)
say(f"  set_func(TONE, 1) rc={rc3c}")
time.sleep(0.2)
rc3d = rig2.set_vfo(VFO_MAIN)
say(f"  set_vfo(VFO_MAIN) rc={rc3d}")
time.sleep(0.2)
rc3e = rig2.set_func(FUNC_TONE, 0)
say(f"  set_func(TONE, 0) on Main rc={rc3e}")
time.sleep(0.5)
show_civ(15)
pause("T mark on Sub (UL)?  No T on Main?")

# ─────────────────────────────────────────────────────────────────────────────
say("\nSTEP 4 — CTCSS OFF on Sub")
say("=" * 65)
rc4a = rig2.set_vfo(VFO_SUB)
rc4b = rig2.set_func(FUNC_TONE, 0)
rc4c = rig2.set_vfo(VFO_MAIN)
say(f"  set_vfo(SUB) rc={rc4a}  set_func(TONE,0) rc={rc4b}  set_vfo(MAIN) rc={rc4c}")
time.sleep(0.5)
show_civ(8)
pause("T mark gone from Sub?")

# ─────────────────────────────────────────────────────────────────────────────
say("\n[SUMMARY]")
say(f"  STEP 1 satmode ON:              rc={rc}")
say(f"  STEP 2 set_mode FM Main/Sub:    rc={rc2a}/{rc2b}")
say(f"  STEP 3 set_vfo(SUB):            rc={rc3a}")
say(f"         set_ctcss_tone(670):     rc={rc3b}")
say(f"         set_func(TONE,1):        rc={rc3c}")
say(f"         set_vfo(MAIN):           rc={rc3d}")
say(f"         set_func(TONE,0) Main:   rc={rc3e}")
say(f"  STEP 4 CTCSS OFF:               rc={rc4b}")

say("\n[CLOSE]")
rig2.close()
_log_fd.close()
say(f"Full Hamlib debug log: {LOG}")
