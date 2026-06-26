#!/usr/bin/env python3
"""IC-9100 CTCSS CI-V diagnostic script.

Tests CTCSS tone setting on Sub band (TX/UL) step by step with long
inter-command delays so timing is not a factor.  After each step the script
pauses and asks the user to check the rig display.

Usage (close FBSAT59 first — port must be free):
  source .venv/bin/activate
  python scripts/test_ic9100_ctcss.py --port /dev/ttyUSB0 --civ 65

Arguments:
  --port   Serial port  (default: /dev/ttyUSB0)
  --baud   Baud rate    (default: 9600)
  --civ    CI-V address in hex without 0x prefix (default: 65 = IC-9100)
  --tone   CTCSS tone in Hz (default: 67.0)
"""

import argparse
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--port", default="/dev/ttyUSB0")
p.add_argument("--baud", type=int, default=9600)
p.add_argument("--civ", default="65")
p.add_argument("--tone", type=float, default=67.0)
args = p.parse_args()

civ = int(args.civ, 16)
ctrl = 0xE0
tone_hz = args.tone


def say(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def pause(prompt: str = "  >> IC-9100の表示を確認してEnterを押して下さい: ") -> None:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    sys.stdin.readline()


def bcd_tone(hz: float) -> bytes:
    val = int(round(hz * 10))
    high = val // 100
    low = val % 100
    return bytes([(high // 10) << 4 | (high % 10), (low // 10) << 4 | (low % 10)])


try:
    import serial
except ImportError:
    say("ERROR: pyserial not found.")
    sys.exit(1)


def frame(*payload: int) -> bytes:
    return bytes([0xFE, 0xFE, civ, ctrl, *payload, 0xFD])


tone_bcd = bcd_tone(tone_hz)

SATMODE_ON = frame(0x16, 0x59, 0x01)
SATMODE_OFF = frame(0x16, 0x59, 0x00)
SELECT_SUB = frame(0x07, 0xD1)
SELECT_MAIN = frame(0x07, 0xD0)
TONE_FREQ = frame(0x1B, 0x00, *tone_bcd)
TONE_ON = frame(0x16, 0x42, 0x01)
TONE_OFF = frame(0x16, 0x42, 0x00)

say("=" * 60)
say("IC-9100 CTCSS CI-V診断スクリプト")
say(f"  port={args.port}  baud={args.baud}  civ=0x{civ:02X}  tone={tone_hz}Hz")
say(f"  BCD: {tone_bcd.hex()}")
say("=" * 60)

ser = serial.Serial(args.port, args.baud, timeout=0.5)
say("ポートを開きました\n")


def send(name: str, data: bytes, wait: float = 0.3) -> None:
    say(f"  送信: {name}  [{data.hex()}]")
    ser.write(data)
    ser.flush()
    time.sleep(wait)
    resp = ser.read(64)
    say(f"  応答: [{resp.hex()}]")


# ── Step 1: SATMODE OFF (初期化) ──────────────────────────────────────────────
say("[Step 1] サットモードOFF (16 59 00)")
send("SATMODE OFF", SATMODE_OFF, wait=0.5)
say("  → IC-9100がノーマルモードになっているはずです")
pause()

# ── Step 2: SATMODE ON ───────────────────────────────────────────────────────
say("\n[Step 2] サットモードON (16 59 01)")
send("SATMODE ON", SATMODE_ON, wait=0.5)
say("  → IC-9100がサットモードになっているはずです（Main=RX, Sub=TX）")
pause()

# ── Step 3: Select Sub ───────────────────────────────────────────────────────
say("\n[Step 3] Subバンド選択 (07 D1)")
send("SELECT SUB", SELECT_SUB, wait=0.3)
say("  → Subバンドがアクティブになっているはずです")
pause()

# ── Step 4: Set tone freq ─────────────────────────────────────────────────────
say(f"\n[Step 4] トーン周波数設定 (1B 00 {tone_bcd.hex()}) = {tone_hz}Hz")
send("TONE FREQ", TONE_FREQ, wait=0.3)

# ── Step 5: TONE ON ──────────────────────────────────────────────────────────
say("\n[Step 5] TONE ON (16 42 01)")
send("TONE ON", TONE_ON, wait=0.3)
say("  → *** TはMainとSubどちらに表示されましたか？ ***")
pause("  >> Main/Sub どちらにTが出ているか入力して下さい (main/sub): ")

# ── Step 6: Select Main ──────────────────────────────────────────────────────
say("\n[Step 6] Mainバンドに戻す (07 D0)")
send("SELECT MAIN", SELECT_MAIN, wait=0.3)
say("  → Mainバンドがアクティブになっているはずです / Tの表示は？")
pause()

# ── Cleanup ──────────────────────────────────────────────────────────────────
say("\n[Cleanup] TONE OFF して終了します")
send("TONE OFF", TONE_OFF, wait=0.3)
ser.close()
say("完了")
