"""
Patch SoapyRTLSDR/Registration.cpp for WinUSB compatibility.

WinUSB cannot provide USB string descriptors via libusb, so
rtlsdr_get_device_usb_strings() returns -1.  The original code does:

    if (rtlsdr_get_device_usb_strings(...) != 0) { continue; }

which skips the device, leaving enumerate() empty and make() failing with
"no match".

This patch rewrites only that if-condition to always be false:

    rtlsdr_get_device_usb_strings(...); /* WinUSB */ if (false) { continue; }

The function is still called so its output buffers are filled with whatever
WinUSB can provide.  The error branch is never taken, so the device is
always kept in the results and matching falls back to device_index.
"""

import re
import sys

with open("SoapyRTLSDR/Registration.cpp", "r") as f:
    content = f.read()

# ── Log the file so CI output shows exactly what we are patching ──────────
print("=== Registration.cpp (lines mentioning key symbols) ===", file=sys.stderr)
for i, line in enumerate(content.splitlines(), 1):
    if any(kw in line for kw in (
        "findRTLSDR", "rtlsdr_get_device_usb_strings", "get_tuner"
    )):
        print(f"  L{i}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

# ── Patch: neutralise the != 0 error check ───────────────────────────────
# Matches single-line and multi-line variants:
#   if (rtlsdr_get_device_usb_strings(i, m, p, s) != 0)
# Replaces with:
#   rtlsdr_get_device_usb_strings(i, m, p, s); /* WinUSB */ if (false)
pattern = (
    r'if\s*\(\s*'
    r'(rtlsdr_get_device_usb_strings\([^)]+\))'
    r'\s*!=\s*0\s*\)'
)
replacement = r'\1; /* WinUSB: ignore error */ if (false)'

patched, n = re.subn(pattern, replacement, content)
print(f"Pattern replacements: {n}", file=sys.stderr)

if n == 0:
    print("ERROR: pattern not matched. Full Registration.cpp follows:", file=sys.stderr)
    print(content, file=sys.stderr)
    sys.exit(1)

with open("SoapyRTLSDR/Registration.cpp", "w") as f:
    f.write(patched)

# ── Log the patched result ────────────────────────────────────────────────
print("=== Patched Registration.cpp (lines mentioning key symbols) ===",
      file=sys.stderr)
for i, line in enumerate(patched.splitlines(), 1):
    if any(kw in line for kw in (
        "findRTLSDR", "rtlsdr_get_device_usb_strings", "get_tuner"
    )):
        print(f"  L{i}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

print("Registration.cpp patched for WinUSB compatibility.")
