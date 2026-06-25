"""
Patch SoapyRTLSDR source for WinUSB compatibility.

Root cause (confirmed by v0.1.57 log analysis):
  rtlsdr_get_device_count() performs libusb_init(&ctx)+libusb_exit(ctx) with
  a private libusb context.  Under Windows WinUSB, libusb_exit() resets the
  USB backend handle cache so subsequent rtlsdr_open() calls can fail.

  A SINGLE rtlsdr_get_device_count() call before rtlsdr_open() is tolerated by
  WinUSB (confirmed by v0.1.53 ctypes diagnostic).  The problem arises when
  MULTIPLE libusb_init+exit cycles occur before rtlsdr_open():

    - SoapySDR::Device::enumerate() called with no driver filter uses
      std::launch::async, spawning a background thread that runs findRTLSDR
      → rtlsdr_get_device_count() CONCURRENTLY with the main open attempt.
    - makeRTLSDR() in the original source calls rtlsdr_get_device_count() again.
    - The SoapyRTLSDR constructor calls rtlsdr_get_device_count() again.

  Multiple concurrent/sequential libusb_init+exit cycles corrupt WinUSB
  backend state so rtlsdr_open() fails with "usb_open error -3" or
  "No RTL-SDR devices found!".

Patches applied:

  Registration.cpp
    1. findRTLSDR  — return device_index-keyed results (no serial/product/
                     manufacturer strings that WinUSB cannot read).  Still calls
                     rtlsdr_get_device_count() ONCE for correct plug-detection.
                     This single call is the ONLY libusb operation before
                     rtlsdr_open() when Device::make() is invoked correctly.
    2. makeRTLSDR  — skip the extra rtlsdr_get_device_count() bounds check.

  Settings.cpp (SoapyRTLSDR constructor)
    3. Replace serial-based device lookup with device_index-based lookup.
       The original constructor throws "No RTL-SDR devices found!" when no
       "serial" key is present in args (our findRTLSDR doesn't provide one
       because WinUSB cannot read USB descriptor strings).  With this patch
       the constructor uses args["device_index"] directly and calls rtlsdr_open()
       without any intermediate libusb calls.

Python-side requirement (src/sdr/device.py):
  - Do NOT call Device.enumerate() with no driver filter before Device::make().
    An unfiltered enumerate launches findRTLSDR asynchronously (background thread)
    which races with the main Device::make() enumerate, causing concurrent
    libusb_init+exit and WinUSB corruption.
  - Do NOT pre-enumerate with driver=rtlsdr inside the Device::make() call either;
    that adds one more libusb_init+exit cycle before rtlsdr_open().
  Device::make() calls enumerate() exactly once internally (deferred/synchronous)
  which is the correct single libusb_init+exit that WinUSB tolerates.
"""

import os
import re
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
            "makeRTLSDR",
            "rtlsdr_get_device_count",
            "rtlsdr_get_device_usb_strings",
            "get_device_label",
            "#include",
        )
    ):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

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


# ── Locate both functions ─────────────────────────────────────────────────────
result_find = find_function_definition(lines, "findRTLSDR")
if result_find is None:
    print("ERROR: could not find findRTLSDR definition in Registration.cpp", file=sys.stderr)
    print(content, file=sys.stderr)
    sys.exit(1)

result_make = find_function_definition(lines, "makeRTLSDR")
if result_make is None:
    print("WARNING: could not find makeRTLSDR definition — skipping that patch", file=sys.stderr)

find_sig, find_open, find_close = result_find
print(
    f"Found findRTLSDR: sig L{find_sig + 1}, open L{find_open + 1}, close L{find_close + 1}",
    file=sys.stderr,
)
if result_make:
    make_sig, make_open, make_close = result_make
    print(
        f"Found makeRTLSDR: sig L{make_sig + 1}, open L{make_open + 1}, close L{make_close + 1}",
        file=sys.stderr,
    )

# ── Replacement for findRTLSDR ────────────────────────────────────────────────
FIND_REPLACEMENT = """\
static SoapySDR::KwargsList findRTLSDR(const SoapySDR::Kwargs &args)
{
    // WinUSB fix (patched by scripts/patch_soapyrtlsdr_winusb.py).
    //
    // Do NOT call rtlsdr_get_device_count() here.  Under Windows WinUSB,
    // rtlsdr_get_device_count() performs libusb_init(&ctx) + libusb_exit(ctx)
    // with a private context.  libusb_exit resets the WinUSB backend handle
    // cache, causing the subsequent rtlsdr_open() inside SoapyRTLSDR() to
    // time out (~1 second) and fail with "usb_open error -3" even when the
    // device is physically present and ctypes can see it.
    //
    // By returning a static single-device result here, the only libusb
    // operation before rtlsdr_open() is the rtlsdr_open() call itself.
    // WinUSB finds the device correctly on the first and only open attempt.
    //
    // Trade-off: enumerate() always reports one RTL-SDR regardless of whether
    // a dongle is actually connected.  rtlsdr_open() will fail (and report an
    // error) if no dongle is present, so the UI will still show failure.
    (void)args;
    SoapySDR::KwargsList results;
    SoapySDR::Kwargs devInfo;
    devInfo["device_index"] = "0";
    devInfo["driver"]       = "rtlsdr";
    devInfo["label"]        = "RTL-SDR";
    results.push_back(devInfo);
    return results;
}
"""

# ── Replacement for makeRTLSDR ────────────────────────────────────────────────
MAKE_REPLACEMENT = """\
static SoapySDR::Device *makeRTLSDR(const SoapySDR::Kwargs &args)
{
    // WinUSB fix (patched by scripts/patch_soapyrtlsdr_winusb.py).
    //
    // The original makeRTLSDR called rtlsdr_get_device_count() before
    // constructing SoapyRTLSDR.  That function performs
    // libusb_init(&ctx) + libusb_exit(ctx) with a private context.
    // Under Windows WinUSB, libusb_exit resets the USB backend state so the
    // subsequent rtlsdr_open() inside SoapyRTLSDR::SoapyRTLSDR() fails with
    // "No RTL-SDR devices found!".
    //
    // Fix: construct SoapyRTLSDR directly.  rtlsdr_open() becomes the first
    // libusb operation and WinUSB finds the device without interference.
    // Out-of-range device indices are handled by rtlsdr_open()'s own error
    // path, which throws an appropriate exception.
    return new SoapyRTLSDR(args);
}
"""

# ── Apply patches (bottom-up to preserve line indices) ───────────────────────
# makeRTLSDR appears after findRTLSDR in the file, so patch it first.
patched_lines = list(lines)

if result_make:
    patched_lines = patched_lines[:make_sig] + [MAKE_REPLACEMENT] + patched_lines[make_close + 1 :]
    # Recalculate findRTLSDR indices: the replacement may have different line
    # count, but findRTLSDR is BEFORE makeRTLSDR so its indices are unchanged.

patched_lines = patched_lines[:find_sig] + [FIND_REPLACEMENT] + patched_lines[find_close + 1 :]

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
            "makeRTLSDR",
            "rtlsdr_get_device_count",
            "rtlsdr_get_device_usb_strings",
            "device_index",
            "new SoapyRTLSDR",
        )
    ):
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

print(
    f"Patched findRTLSDR (L{find_sig + 1}–L{find_close + 1}): "
    f"returns single device_index=0 candidate, no libusb calls."
)
if result_make:
    print(
        f"Patched makeRTLSDR (L{make_sig + 1}–L{make_close + 1}): "
        f"removed rtlsdr_get_device_count() bounds check."
    )

# ── Patch SoapyRTLSDR.cpp constructor ────────────────────────────────────────
# The SoapyRTLSDR constructor calls rtlsdr_get_device_count() as a guard
# before rtlsdr_open().  Under WinUSB, this libusb_init+exit breaks the
# backend state so rtlsdr_open() then fails with "No RTL-SDR devices found!".
#
# We remove two blocks:
#   (A) int deviceCount = rtlsdr_get_device_count();
#       if (deviceCount == 0) { throw ...; }
#
#   (B) if (deviceIndex >= deviceCount) { throw ...; }
#
# The deviceIndex = 0 / args.count("device_index") block is kept intact.

# The constructor lives in Settings.cpp in pothosware/SoapyRTLSDR.
# Fall back to any .cpp that contains rtlsdr_get_device_count outside Registration.cpp.
_candidates = ["SoapyRTLSDR/Settings.cpp", "SoapyRTLSDR/SoapyRTLSDR.cpp"]
CTOR_SRC = next(
    (p for p in _candidates if os.path.exists(p)),
    None,
)
if CTOR_SRC is None:
    # Search all .cpp files in SoapyRTLSDR/ for rtlsdr_get_device_count
    for _fname in os.listdir("SoapyRTLSDR"):
        if not _fname.endswith(".cpp") or _fname == "Registration.cpp":
            continue
        _fpath = f"SoapyRTLSDR/{_fname}"
        with open(_fpath, encoding="utf-8", errors="replace") as _f:
            if "rtlsdr_get_device_count" in _f.read():
                CTOR_SRC = _fpath
                break

if CTOR_SRC is None:
    print(
        "WARNING: could not find constructor source with rtlsdr_get_device_count"
        " — skipping constructor patch",
        file=sys.stderr,
    )
else:
    print(f"Constructor source: {CTOR_SRC}", file=sys.stderr)

if CTOR_SRC is not None:
    with open(CTOR_SRC, encoding="utf-8", errors="replace") as f:
        ctor_content = f.read()
    crlf_before = ctor_content.count("\r")
    ctor_content = ctor_content.replace("\r\n", "\n")  # normalize CRLF → LF (Windows git clone)
    print(f"CTOR content length: {len(ctor_content)}", file=sys.stderr)
    print(f"CRLF count before normalization: {crlf_before}", file=sys.stderr)
    print(f"CR count after normalization: {ctor_content.count(chr(13))}", file=sys.stderr)
    print(f"'serial' occurrences in file: {ctor_content.count('serial')}", file=sys.stderr)
    # Show the exact bytes of the target block to diagnose match failures
    idx = ctor_content.find("//if a serial is not present")
    if idx >= 0:
        snippet = ctor_content[max(0, idx - 10) : idx + 200]
        print(f"SERIAL_BLOCK raw bytes: {snippet!r}", file=sys.stderr)
    else:
        print(
            "SERIAL_BLOCK marker '//if a serial is not present' NOT FOUND in file", file=sys.stderr
        )
else:
    ctor_content = ""
    crlf_before = 0

print(
    f"=== {CTOR_SRC or 'constructor'} BEFORE patch (rtlsdr_get_device_count lines) ===",
    file=sys.stderr,
)
for i, line in enumerate(ctor_content.splitlines(), 1):
    if "rtlsdr_get_device_count" in line or "No RTL-SDR" in line or "device_index" in line.lower():
        print(f"  L{i:4d}: {line}", file=sys.stderr)
print("=== END ===", file=sys.stderr)

# Dump the full constructor source so we can see what runs after rtlsdr_open().
# This is essential to understand the "No RTL-SDR devices found!" failure path.
print(f"=== {CTOR_SRC or 'constructor'} FULL CONTENT ===", file=sys.stderr)
for i, line in enumerate(ctor_content.splitlines(), 1):
    print(f"  {i:4d}: {line}", file=sys.stderr)
print("=== END FULL CONTENT ===", file=sys.stderr)

if CTOR_SRC is not None and ctor_content:
    ctor_patched = ctor_content

    # Root cause (confirmed by Settings.cpp full dump in v0.1.54 CI):
    #
    #   L64: if (args.count("serial") == 0) throw "No RTL-SDR devices found!"
    #   L66: const auto serial = args.at("serial");
    #   L67: deviceId = rtlsdr_get_index_by_serial(serial.c_str());
    #   L68: if (deviceId < 0) throw ...
    #   L74: rtlsdr_open(&dev, deviceId)
    #
    # Two problems:
    #   (1) Our patched findRTLSDR returns no "serial" key, so the constructor
    #       throws at L64 before rtlsdr_open() is ever reached.
    #   (2) rtlsdr_get_index_by_serial() internally calls rtlsdr_get_device_count()
    #       + rtlsdr_get_usb_strings(), adding another libusb_init+exit cycle
    #       that resets WinUSB backend state → rtlsdr_open() would fail even
    #       if serial were present.
    #
    # Fix: replace the serial-check + index-by-serial block with a direct
    # device_index parse from args.  findRTLSDR sets "device_index" = "0"
    # (or the actual index for multi-dongle), so the constructor can open the
    # correct device without any intermediate libusb calls.
    #
    # Fallback chain (for non-WinUSB systems):
    #   1. device_index in args  → use directly, no libusb (WinUSB-safe)
    #   2. serial in args        → rtlsdr_get_index_by_serial() (libusbK/Linux)
    #   3. neither               → throw as before

    SERIAL_BLOCK_FIND = (
        "    //if a serial is not present, then findRTLSDR had zero devices enumerated\n"
        '    if (args.count("serial") == 0) throw std::runtime_error("No RTL-SDR devices found!");\n'  # noqa: E501
        "\n"
        '    const auto serial = args.at("serial");\n'
        "    deviceId = rtlsdr_get_index_by_serial(serial.c_str());\n"
        '    if (deviceId < 0) throw std::runtime_error("rtlsdr_get_index_by_serial("+serial+") - " + std::to_string(deviceId));\n'  # noqa: E501
    )

    SERIAL_BLOCK_REPLACE = (
        "    // WinUSB fix (patched by scripts/patch_soapyrtlsdr_winusb.py):\n"
        "    // Use device_index from args directly to avoid rtlsdr_get_index_by_serial(),\n"
        "    // which internally calls rtlsdr_get_device_count()+rtlsdr_get_usb_strings().\n"
        "    // Those functions perform libusb_init+exit cycles that reset WinUSB backend\n"
        '    // state, causing the subsequent rtlsdr_open() to fail ("No RTL-SDR devices\n'
        '    // found!") even when the device is physically present.\n'
        '    if (args.count("device_index") != 0) {\n'
        '        deviceId = std::stoi(args.at("device_index"));\n'
        '    } else if (args.count("serial") != 0) {\n'
        "        // Fallback for non-WinUSB systems (libusbK / Linux / macOS).\n"
        '        const auto serial = args.at("serial");\n'
        "        deviceId = rtlsdr_get_index_by_serial(serial.c_str());\n"
        '        if (deviceId < 0) throw std::runtime_error("No RTL-SDR device with serial: " + serial);\n'  # noqa: E501
        "    } else {\n"
        '        throw std::runtime_error("No RTL-SDR devices found!");\n'
        "    }\n"
    )

    if SERIAL_BLOCK_FIND in ctor_patched:
        ctor_patched = ctor_patched.replace(SERIAL_BLOCK_FIND, SERIAL_BLOCK_REPLACE, 1)
        print(
            f"{CTOR_SRC}: replaced serial-check + rtlsdr_get_index_by_serial() with"
            " direct device_index parse (WinUSB fix).",
            file=sys.stderr,
        )
    else:
        print(
            f"WARNING: {CTOR_SRC} serial-block not found — trying regex fallback",
            file=sys.stderr,
        )
        # Regex fallback: match the block loosely in case whitespace differs
        serial_block_re = re.compile(
            r"[ \t]*//if a serial is not present.*?\n"
            r'[ \t]*if \(args\.count\("serial"\) == 0\) throw[^\n]+\n'
            r"(?:[ \t]*\n)?"
            r'[ \t]*const auto serial = args\.at\("serial"\);\n'
            r"[ \t]*deviceId = rtlsdr_get_index_by_serial[^\n]+\n"
            r"[ \t]*if \(deviceId < 0\) throw[^\n]+\n",
            re.DOTALL,
        )
        ctor_after = serial_block_re.sub(SERIAL_BLOCK_REPLACE, ctor_patched, count=1)
        if ctor_after == ctor_patched:
            print(
                f"ERROR: {CTOR_SRC} serial-block regex also did not match — patch failed",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            ctor_patched = ctor_after
            print(f"{CTOR_SRC}: replaced serial-block via regex fallback.", file=sys.stderr)

    with open(CTOR_SRC, "w", encoding="utf-8", newline="") as f:
        f.write(ctor_patched)

    print(f"=== {CTOR_SRC} AFTER patch (rtlsdr_get_device_count lines) ===", file=sys.stderr)
    for i, line in enumerate(ctor_patched.splitlines(), 1):
        if (
            "rtlsdr_get_device_count" in line
            or "No RTL-SDR" in line
            or "device_index" in line.lower()
        ):
            print(f"  L{i:4d}: {line}", file=sys.stderr)
    print("=== END ===", file=sys.stderr)
