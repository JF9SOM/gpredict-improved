#!/usr/bin/env python3
"""Test script: FTX-1F Direct mode split (TX VFO) control.

Verifies:
  1. FT1; sets VFO-B (Sub) as TX
  2. FT0; sets VFO-A (Main) as TX  (used on app exit)
  3. Frequencies can be set on both VFOs via FA/FB CAT

Usage:
    python3 scripts/test_ftx1_split.py [PORT] [BAUD]

Defaults: PORT=/dev/ttyUSB0  BAUD=38400

The FTX-1F uses FT0/FT1 for TX VFO selection, unlike FT-991A (FT2/FT3).
After the test, the rig is left with VFO-A as TX (safe state for simplex).
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 38400

# RS-44 transponder (cross-band SSB)
TEST_DL_HZ = 435_610_000   # DL → VFO-A (RX)
TEST_UL_HZ = 145_935_000   # UL → VFO-B (TX)

print(f"Port: {PORT}  Baud: {BAUD}")
print(f"Test DL: {TEST_DL_HZ / 1e6:.3f} MHz (VFO-A/Main)")
print(f"Test UL: {TEST_UL_HZ / 1e6:.3f} MHz (VFO-B/Sub)")
print()


# ── helpers ───────────────────────────────────────────────────────────────────

def send(cmd: bytes) -> None:
    with serial.Serial(PORT, BAUD, timeout=0.5) as s:
        s.write(cmd)
    time.sleep(0.12)


def query(cmd: bytes, label: str = "") -> bytes:
    with serial.Serial(PORT, BAUD, timeout=0.5) as s:
        s.write(cmd)
        time.sleep(0.12)
        r = s.read(64)
    tag = label or cmd.decode(errors="replace")
    print(f"  {tag}: {r!r}")
    return r


def check(resp: bytes, expected_prefix: bytes, label: str) -> bool:
    ok = resp.startswith(expected_prefix)
    status = "OK" if ok else "FAIL"
    if not ok:
        print(f"  *** {label}: expected prefix {expected_prefix!r}, got {resp!r}")
    return ok


# ── STEP 0: initial state ─────────────────────────────────────────────────────
print("=" * 60)
print("STEP 0: Query initial state")
ft_before = query(b"FT;", "FT (TX VFO before)")
fa_before = query(b"FA;", "FA (VFO-A freq before)")
fb_before = query(b"FB;", "FB (VFO-B freq before)")
print()

# ── STEP 1: Set VFO-A = DL, VFO-B = UL via FA/FB ────────────────────────────
print("=" * 60)
print(f"STEP 1: Set VFO-A to DL {TEST_DL_HZ / 1e6:.3f} MHz via FA command")
send(f"FA{TEST_DL_HZ:09d};".encode())
fa_set = query(b"FA;", "FA after set")
check(fa_set, f"FA{TEST_DL_HZ:09d}".encode(), "VFO-A DL freq")
print()

print(f"STEP 1b: Set VFO-B to UL {TEST_UL_HZ / 1e6:.3f} MHz via FB command")
send(f"FB{TEST_UL_HZ:09d};".encode())
fb_set = query(b"FB;", "FB after set")
check(fb_set, f"FB{TEST_UL_HZ:09d}".encode(), "VFO-B UL freq")
print()

# ── STEP 2: FT1; — set VFO-B (Sub) as TX ─────────────────────────────────────
print("=" * 60)
print("STEP 2: Enable split — FT1; (VFO-B as TX)")
send(b"FT1;")
ft_after = query(b"FT;", "FT after FT1")
ok = check(ft_after, b"FT1", "VFO-B is TX")
if ok:
    print("  >>> PASS: VFO-B (Sub) is now TX ✓")
else:
    print("  >>> FAIL: FT1; did not switch TX to VFO-B")
print()
print("  *** Check the rig display: VFO-B / Sub should show as the active TX VFO ***")
print("  *** VFO-A should show {:.3f} MHz (DL/RX) ***".format(TEST_DL_HZ / 1e6))
print("  *** VFO-B should show {:.3f} MHz (UL/TX) ***".format(TEST_UL_HZ / 1e6))
input("  Press ENTER to continue to exit test...")
print()

# ── STEP 3: FT0; — restore VFO-A (Main) as TX (app exit behaviour) ───────────
print("=" * 60)
print("STEP 3: App-exit restore — FT0; (VFO-A / Main as TX)")
send(b"FT0;")
ft_restored = query(b"FT;", "FT after FT0")
ok2 = check(ft_restored, b"FT0", "VFO-A is TX")
if ok2:
    print("  >>> PASS: VFO-A (Main) is now TX ✓")
else:
    print("  >>> FAIL: FT0; did not restore TX to VFO-A")
print()
print("  *** Check the rig display: VFO-A / Main should be active TX VFO ***")
input("  Press ENTER to finish...")
print()

# ── STEP 4: Verify FT2/FT3 are not supported (FT-991A commands) ──────────────
print("=" * 60)
print("STEP 4: Confirm FT2; / FT3; are NOT supported on FTX-1F (expected: ignored)")
send(b"FT2;")
ft2 = query(b"FT;", "FT after FT2 (should stay FT0)")
send(b"FT3;")
ft3 = query(b"FT;", "FT after FT3 (should stay FT0)")
print("  (FT2/FT3 should have no effect — FTX-1F ignores them)")
print()

# ── Summary ───────────────────────────────────────────────────────────────────
print("=" * 60)
print("Summary")
print(f"  FT before test : {ft_before!r}")
print(f"  FT after FT1;  : {ft_after!r}  (expect FT1 = VFO-B TX)")
print(f"  FT after FT0;  : {ft_restored!r}  (expect FT0 = VFO-A TX)")
print(f"  FT after FT2;  : {ft2!r}  (expect unchanged = FT0)")
print(f"  FT after FT3;  : {ft3!r}  (expect unchanged = FT0)")
print()
all_ok = (
    check(ft_after, b"FT1", "split ON (VFO-B TX)")
    and check(ft_restored, b"FT0", "exit restore (VFO-A TX)")
)
if all_ok:
    print("=== ALL TESTS PASSED ===")
else:
    print("=== SOME TESTS FAILED — review output above ===")
