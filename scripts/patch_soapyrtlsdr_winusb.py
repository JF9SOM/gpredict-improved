"""
Patch SoapyRTLSDR/Registration.cpp for WinUSB compatibility.

WinUSB cannot read USB string descriptors via libusb, so
rtlsdr_get_device_usb_strings() returns -1 and leaves buffers
with garbage data.  SoapyRTLSDR's findRTLSDR() then:
  1. hits the "!= 0" guard and skips the device, OR
  2. continues with garbage serial → get_device_label() receives
     invalid data and may throw → enumerate() returns []

Strategy: insert a safe wrapper function + #define AFTER the last
#include in the file.  The wrapper (a) calls the original function,
(b) zero-terminates all output buffers when the call fails, (c) always
returns 0.  Because the #define appears before findRTLSDR(), every call
inside that function is transparently redirected to the wrapper:
  - "!= 0" check always evaluates false  → device never skipped
  - serial/product/manufact are always valid empty strings on failure
  - get_device_label(i, "") works fine  → enumerate() returns the device
"""

import sys

SRC = "SoapyRTLSDR/Registration.cpp"

with open(SRC, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

# ── Log key lines before patching ────────────────────────────────────────
print("=== Registration.cpp BEFORE patch (key lines) ===", file=sys.stderr)
for i, line in enumerate(content.splitlines(), 1):
    if any(kw in line for kw in (
        "findRTLSDR", "rtlsdr_get_device_usb_strings",
        "get_tuner", "get_device_label", "#include",
    )):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

# ── Find insertion point: just after the last #include line ───────────────
lines = content.splitlines(keepends=True)
last_include_idx = -1
for i, line in enumerate(lines):
    if line.lstrip().startswith("#include"):
        last_include_idx = i

if last_include_idx == -1:
    print("ERROR: no #include found in Registration.cpp", file=sys.stderr)
    print(content, file=sys.stderr)
    sys.exit(1)

# ── The wrapper to inject ─────────────────────────────────────────────────
WRAPPER = """\

// ── WinUSB compatibility (injected by scripts/patch_soapyrtlsdr_winusb.py) ──
// WinUSB cannot provide USB string descriptors. rtlsdr_get_device_usb_strings()
// returns -1 under WinUSB and may leave buffers with garbage. We redirect all
// calls through _rtlsdr_usb_strings_safe which (a) calls the real function,
// (b) zero-terminates outputs when it fails, (c) always returns 0 so that the
// "!= 0" guard in findRTLSDR() never skips the device.
static int _rtlsdr_usb_strings_safe(
    uint32_t index, char *manufact, char *product, char *serial)
{
    if (rtlsdr_get_device_usb_strings(index, manufact, product, serial) != 0)
    {
        if (manufact) manufact[0] = '\\0';
        if (product)  product[0]  = '\\0';
        if (serial)   serial[0]   = '\\0';
    }
    return 0;
}
#define rtlsdr_get_device_usb_strings _rtlsdr_usb_strings_safe
// ─────────────────────────────────────────────────────────────────────────

"""

# Insert the wrapper after the last #include line
patched_lines = (
    lines[:last_include_idx + 1]
    + [WRAPPER]
    + lines[last_include_idx + 1:]
)
patched = "".join(patched_lines)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(patched)

# ── Log key lines after patching ─────────────────────────────────────────
print("=== Registration.cpp AFTER patch (key lines) ===", file=sys.stderr)
for i, line in enumerate(patched.splitlines(), 1):
    if any(kw in line for kw in (
        "findRTLSDR", "rtlsdr_get_device_usb_strings",
        "_rtlsdr_usb_strings_safe", "get_tuner", "get_device_label",
    )):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

print(f"Inserted WinUSB wrapper after line {last_include_idx + 1} of Registration.cpp")
