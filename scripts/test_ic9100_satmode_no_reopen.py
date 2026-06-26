#!/usr/bin/env python3
"""Test: is close→open cycle necessary, or does set_func(SATMODE,1) alone set cache?

Current connect() sequence:
  open() → set_func(SATMODE,1) → close() → open() → set_freq(VFO_TX, ...)

Question: can we simplify to:
  open() → set_func(SATMODE,1) → set_freq(VFO_TX, ...)  (no close/reopen)

If set_func(SATMODE,1) updates cache->satmode=1 immediately, close→open is
unnecessary. This would also fix IC-9700 where the read-back during open()
may not correctly detect satmode.

Run with FBSAT59 CLOSED:
  source .venv/bin/activate
  python scripts/test_ic9100_satmode_no_reopen.py [--port /dev/ttyUSB0] [--civ 65]
"""

import argparse
import os
import sys
import time

LOG = "/tmp/test_satmode_no_reopen.log"

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

_log_fd = open(LOG, "w")  # noqa: SIM115
os.dup2(_log_fd.fileno(), 2)


def say(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def pause(label: str = "") -> None:
    sys.stdout.write(f"  >> {label}  [Enter] ")
    sys.stdout.flush()
    sys.stdin.readline()


def show_civ(n: int = 10) -> None:
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
            "05 ",
            "07 ",
            "satmode",
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

VFO_MAIN = int(Hamlib.RIG_VFO_MAIN)
VFO_TX = int(Hamlib.RIG_VFO_TX)
SATMODE = Hamlib.RIG_FUNC_SATMODE

say("=" * 65)
say("SAT mode: no-reopen test (set_func alone sufficient?)")
say(f"  port={args.port}  baud={args.baud}  civ={civ_hex}  model={args.model}")
say(f"  DL={DL / 1e6:.3f} MHz   UL base={UL0 / 1e6:.3f} MHz")
say(f"  Hamlib debug → {LOG}")
say("=" * 65)

# ─────────────────────────────────────────────────────────────────────────────
say("\n[Pattern A] close→open cycle (current implementation — should always work)")
say("  open() → set_func(SATMODE,1) → close() → open() → set_freq(VFO_TX, UL)")
say("=" * 65)

rig_a = make_rig()
rig_a.open()
time.sleep(0.3)
rc_sat_a = rig_a.set_func(SATMODE, 1)
say(f"  set_func(SATMODE,1) rc={rc_sat_a}")
time.sleep(0.4)
rig_a.close()
time.sleep(0.3)

rig_a2 = make_rig()
rig_a2.open()
time.sleep(0.3)
say("  close→open done  (cache->satmode should be 1 via read-back)")

UL_A = UL0
rc_dl_a = rig_a2.set_freq(VFO_MAIN, DL)
say(f"  set_freq(VFO_MAIN, {DL}) rc={rc_dl_a}")
time.sleep(0.2)
rc_ul_a = rig_a2.set_freq(VFO_TX, UL_A)
say(f"  set_freq(VFO_TX,  {UL_A}) rc={rc_ul_a}")
time.sleep(0.4)
show_civ(8)
pause(f"[Pattern A] Sub={UL_A / 1e6:.3f} MHz?  rc_ul={rc_ul_a}")

UL_A2 = UL0 + 5_000
rc_ul_a2 = rig_a2.set_freq(VFO_TX, UL_A2)
say(f"  set_freq(VFO_TX, {UL_A2}) rc={rc_ul_a2}  (+5kHz)")
time.sleep(0.3)
pause(f"[Pattern A] Sub={UL_A2 / 1e6:.3f} MHz? (+5kHz)")

rig_a2.close()
time.sleep(0.5)

# ─────────────────────────────────────────────────────────────────────────────
say("\n[Pattern B] NO close→open — set_func then immediately set_freq")
say("  open() → set_func(SATMODE,1) → set_freq(VFO_TX, UL)  (no close/reopen)")
say("=" * 65)

rig_b = make_rig()
rig_b.open()
time.sleep(0.3)
rc_sat_b = rig_b.set_func(SATMODE, 1)
say(f"  set_func(SATMODE,1) rc={rc_sat_b}  (does this set cache->satmode=1?)")
time.sleep(0.4)
show_civ(6)

UL_B = UL0 + 10_000
rc_dl_b = rig_b.set_freq(VFO_MAIN, DL)
say(f"  set_freq(VFO_MAIN, {DL}) rc={rc_dl_b}")
time.sleep(0.2)
rc_ul_b = rig_b.set_freq(VFO_TX, UL_B)
say(f"  set_freq(VFO_TX,  {UL_B}) rc={rc_ul_b}")
time.sleep(0.4)
show_civ(8)
pause(f"[Pattern B] Sub={UL_B / 1e6:.3f} MHz?  rc_ul={rc_ul_b}  (no reopen)")

UL_B2 = UL0 + 15_000
rc_ul_b2 = rig_b.set_freq(VFO_TX, UL_B2)
say(f"  set_freq(VFO_TX, {UL_B2}) rc={rc_ul_b2}  (+15kHz)")
time.sleep(0.3)
pause(f"[Pattern B] Sub={UL_B2 / 1e6:.3f} MHz? (+15kHz)")

rig_b.close()
time.sleep(0.5)

# ─────────────────────────────────────────────────────────────────────────────
say("\n[Pattern C] close→open AND set_func again after reopen")
say("  open() → set_func(SATMODE,1) → close() → open() → set_func(SATMODE,1) → set_freq")
say("  (proposed fix for IC-9700 where read-back may not set cache->satmode)")
say("=" * 65)

rig_c = make_rig()
rig_c.open()
time.sleep(0.3)
rc_sat_c1 = rig_c.set_func(SATMODE, 1)
say(f"  set_func(SATMODE,1) [1st] rc={rc_sat_c1}")
time.sleep(0.4)
rig_c.close()
time.sleep(0.3)

rig_c2 = make_rig()
rig_c2.open()
time.sleep(0.3)
rc_sat_c2 = rig_c2.set_func(SATMODE, 1)
say(f"  set_func(SATMODE,1) [2nd, after reopen] rc={rc_sat_c2}")
say("  (forces cache->satmode=1 regardless of read-back)")
time.sleep(0.4)
show_civ(6)

UL_C = UL0 + 20_000
rc_dl_c = rig_c2.set_freq(VFO_MAIN, DL)
say(f"  set_freq(VFO_MAIN, {DL}) rc={rc_dl_c}")
time.sleep(0.2)
rc_ul_c = rig_c2.set_freq(VFO_TX, UL_C)
say(f"  set_freq(VFO_TX,  {UL_C}) rc={rc_ul_c}")
time.sleep(0.4)
show_civ(8)
pause(f"[Pattern C] Sub={UL_C / 1e6:.3f} MHz?  rc_ul={rc_ul_c}")

UL_C2 = UL0 + 25_000
rc_ul_c2 = rig_c2.set_freq(VFO_TX, UL_C2)
say(f"  set_freq(VFO_TX, {UL_C2}) rc={rc_ul_c2}  (+25kHz)")
time.sleep(0.3)
pause(f"[Pattern C] Sub={UL_C2 / 1e6:.3f} MHz? (+25kHz)")

rig_c2.close()

# ─────────────────────────────────────────────────────────────────────────────
say("\n" + "=" * 65)
say("[SUMMARY]")
say(f"  Pattern A (close→open):              sat rc={rc_sat_a}  ul rc={rc_ul_a}/{rc_ul_a2}")
say(f"  Pattern B (no reopen):               sat rc={rc_sat_b}  ul rc={rc_ul_b}/{rc_ul_b2}")
say(f"  Pattern C (close→open + set_func×2): sat rc={rc_sat_c1}/{rc_sat_c2}"
    f"  ul rc={rc_ul_c}/{rc_ul_c2}")
say("")
say("  判定:")
say("  B が動く → set_func だけで cache->satmode=1 になる → close/open 不要")
say("             → connect() を大幅に簡略化できる (IC-9700 も同様に動くはず)")
say("  B が動かない → close→open が必要 → Pattern C (set_func×2) で IC-9700 も修正")
say("  C が動く → IC-9700 向け修正として close→open 後に set_func を追加")
say("=" * 65)

_log_fd.close()
say(f"\nFull Hamlib debug log: {LOG}")
