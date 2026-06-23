"""
Patch SoapyRTLSDR/Registration.cpp for WinUSB compatibility.

Strategy: replace the entire findRTLSDR() body with a minimal implementation
that uses only rtlsdr_get_device_count() and rtlsdr_get_device_name() —
no USB string-descriptor reading, no device open inside findRTLSDR.

This fixes two WinUSB-specific problems:
  1. The original findRTLSDR calls rtlsdr_get_device_usb_strings() for serial.
     Under WinUSB this may set serial="" (or garbage), causing the serial-filter
     check to skip the device → enumerate() returns [].
  2. The original findRTLSDR opens the device inside get_device_label() to
     detect the tuner type.  On Windows/WinUSB the resulting libusb handle may
     linger, preventing the subsequent makeRTLSDR() rtlsdr_open() from
     succeeding → make() "no match".

The replacement findRTLSDR:
  - Never calls rtlsdr_get_device_usb_strings()  → no descriptor issues
  - Never opens the device                        → no handle leakage
  - Filters only by device_index (if in args)    → serial/label matching skipped
  - Returns {device_index, driver, label} kwargs → makeRTLSDR opens by index
"""

import sys

SRC = "SoapyRTLSDR/Registration.cpp"

with open(SRC, encoding="utf-8", errors="replace") as f:
    content = f.read()

# ── Log key lines before patching ────────────────────────────────────────────
print("=== Registration.cpp BEFORE patch (key lines) ===", file=sys.stderr)
for i, line in enumerate(content.splitlines(), 1):
    if any(
        kw in line
        for kw in (
            "findRTLSDR",
            "rtlsdr_get_device_usb_strings",
            "get_tuner",
            "get_device_label",
            "#include",
        )
    ):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

# ── Find findRTLSDR definition using brace-counting ──────────────────────────
# We look for the DEFINITION (has '{' before ';' on/after the signature line),
# not the forward declaration (ends with ';').

lines = content.splitlines(keepends=True)


def find_function_definition(lines: list[str], func_name: str) -> tuple[int, int, int] | None:
    """Return (def_start_idx, body_open_idx, body_close_idx) for func_name.

    def_start_idx  : index of the line containing the function signature
    body_open_idx  : index of the line with the opening '{'
    body_close_idx : index of the line with the matching closing '}'

    Returns None if not found.
    """
    i = 0
    while i < len(lines):
        line = lines[i]
        if func_name not in line:
            i += 1
            continue
        # Skip occurrences inside // line comments
        comment_pos = line.find("//")
        func_pos = line.find(func_name)
        if comment_pos != -1 and comment_pos < func_pos:
            i += 1
            continue
        # Candidate: line contains func_name outside a comment.
        # Scan forward to determine if this is a definition ('{' before ';').
        sig_start = i
        j = i
        found_open = None
        while j < len(lines):
            chunk = lines[j]
            brace_pos = chunk.find("{")
            semi_pos = chunk.find(";")
            if brace_pos != -1 and (semi_pos == -1 or brace_pos < semi_pos):
                found_open = j
                break
            if semi_pos != -1:
                # Forward declaration — skip and keep searching
                break
            j += 1
        if found_open is None:
            i += 1
            continue
        # Brace-count from the opening '{' to find the matching '}'
        depth = 0
        body_close = None
        for k in range(found_open, len(lines)):
            for ch in lines[k]:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        body_close = k
                        break
            if body_close is not None:
                break
        if body_close is None:
            i += 1
            continue
        return (sig_start, found_open, body_close)
    return None


result = find_function_definition(lines, "findRTLSDR")
if result is None:
    print("ERROR: could not find findRTLSDR definition in Registration.cpp", file=sys.stderr)
    print(content, file=sys.stderr)
    sys.exit(1)

sig_start, body_open, body_close = result
print(
    f"Found findRTLSDR: sig L{sig_start + 1}, open L{body_open + 1}, close L{body_close + 1}",
    file=sys.stderr,
)

# ── Replacement function ──────────────────────────────────────────────────────
REPLACEMENT = """\
static SoapySDR::KwargsList findRTLSDR(const SoapySDR::Kwargs &args)
{
    // WinUSB-compatible implementation (patched by scripts/patch_soapyrtlsdr_winusb.py).
    //
    // The original findRTLSDR called rtlsdr_get_device_usb_strings() for the
    // serial number and opened the device inside get_device_label() to detect
    // the tuner type.  Both operations cause failures under Windows WinUSB:
    //   - USB string-descriptor reads via libusb may fail, leaving serial=""
    //     so the serial-filter check skips the device → enumerate() returns [].
    //   - Opening the device in findRTLSDR leaves a lingering libusb handle that
    //     blocks the subsequent makeRTLSDR() rtlsdr_open() → make() "no match".
    //
    // This replacement enumerates devices by index only:
    //   - No rtlsdr_get_device_usb_strings() call  → no descriptor issues
    //   - No device open                            → no handle leakage
    //   - Filters only by device_index if provided → serial/label matching skipped
    SoapySDR::KwargsList results;
    const int n = rtlsdr_get_device_count();
    for (int i = 0; i < n; i++)
    {
        if (args.count("device_index") != 0 &&
            std::stoi(args.at("device_index")) != i) continue;
        SoapySDR::Kwargs devInfo;
        devInfo["device_index"] = std::to_string(i);
        devInfo["driver"] = "rtlsdr";
        devInfo["label"] = std::string(rtlsdr_get_device_name(i));
        results.push_back(devInfo);
    }
    return results;
}
"""

# Replace from sig_start through body_close (inclusive) with new implementation
patched_lines = lines[:sig_start] + [REPLACEMENT] + lines[body_close + 1 :]
patched = "".join(patched_lines)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(patched)

# ── Log key lines after patching ─────────────────────────────────────────────
print("=== Registration.cpp AFTER patch (key lines) ===", file=sys.stderr)
for i, line in enumerate(patched.splitlines(), 1):
    if any(
        kw in line
        for kw in (
            "findRTLSDR",
            "rtlsdr_get_device_usb_strings",
            "_rtlsdr_usb_strings_safe",
            "get_tuner",
            "get_device_label",
            "device_index",
            "rtlsdr_get_device_count",
            "rtlsdr_get_device_name",
        )
    ):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

print(
    f"Replaced findRTLSDR (L{sig_start + 1}–L{body_close + 1}) "
    f"with WinUSB-compatible minimal implementation."
)
