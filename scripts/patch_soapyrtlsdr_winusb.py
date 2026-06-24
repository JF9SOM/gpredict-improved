"""
Patch SoapyRTLSDR source for WinUSB compatibility.

Root cause (confirmed by log analysis):
  rtlsdr_get_device_count() performs libusb_init(&ctx)+libusb_exit(ctx) with
  a private libusb context.  Under Windows WinUSB, libusb_exit() resets the
  USB backend handle cache.  Any subsequent libusb_init() + device enumeration
  fails to find the device.  Under libusbK a kernel-mode filter driver
  maintains state independently, so this is not an issue there.

  rtlsdr_get_device_count() is called in THREE places:
    (a) Registration.cpp  makeRTLSDR()         — bounds check before make
    (b) SoapyRTLSDR.cpp   SoapyRTLSDR ctor     — bounds check + "no devices" guard
    (c) Registration.cpp  findRTLSDR()          — device enumeration loop

  Under WinUSB the call chain for one SoapySDR Device::make() attempt is:
    1. findRTLSDR (patched: no libusb calls)
    2. makeRTLSDR (patched: no libusb calls) → new SoapyRTLSDR(args)
    3. SoapyRTLSDR ctor:
         SoapySDR_log("Opening RTL-SDR...")    ← first log line
         rtlsdr_get_device_count()             ← libusb_init+exit, WinUSB broken
         if (count==0) throw "No RTL-SDR..."  ← FAILS HERE (count now 0)
         rtlsdr_open(...)                      ← never reached

Patches applied:

  Registration.cpp
    1. findRTLSDR  — return a single device_index=0 candidate with no libusb
                     calls.  Also fixes the "4 phantom entries" in SDR dropdown.
    2. makeRTLSDR  — skip rtlsdr_get_device_count() bounds check entirely.

  SoapyRTLSDR.cpp
    3. SoapyRTLSDR ctor — remove the rtlsdr_get_device_count() guard block so
                          that rtlsdr_open() is the FIRST libusb operation.
                          WinUSB finds the device successfully.
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
    // Do NOT call rtlsdr_get_device_count() or any other libusb/librtlsdr
    // function here.  Any libusb_init(&ctx)+libusb_exit(ctx) cycle resets
    // the WinUSB backend handle cache; the subsequent rtlsdr_open() inside
    // makeRTLSDR then fails to enumerate the device ("No RTL-SDR devices
    // found!").  Under libusbK this is not an issue.
    //
    // Return a single device_index=0 candidate unconditionally.
    // SoapySDR::Device::make() will select it and call makeRTLSDR (see
    // below), which opens the device directly via rtlsdr_open() as the
    // first libusb operation — WinUSB finds it successfully.
    //
    // Users with multiple RTL-SDR dongles: only device 0 appears in the
    // dropdown.  This is an acceptable limitation of the WinUSB driver path.
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
else:
    ctor_content = ""

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

    # Block A: remove "int deviceCount = rtlsdr_get_device_count();" and the
    # immediately following "if (deviceCount == 0) { throw ...; }" block.
    # Pattern is flexible about whitespace / brace placement.
    block_a = re.compile(
        r"[ \t]+int deviceCount = rtlsdr_get_device_count\(\);\n"
        r"(?:[ \t]*\n)*"  # optional blank lines between statements
        r"[ \t]+if \(deviceCount == 0\) \{[^}]*\}\n",
        re.DOTALL,
    )
    ctor_after_a = block_a.sub(
        "    // WinUSB fix: removed rtlsdr_get_device_count() guard"
        " (see scripts/patch_soapyrtlsdr_winusb.py)\n",
        ctor_patched,
        count=1,
    )
    if ctor_after_a == ctor_patched:
        print(
            f"WARNING: {CTOR_SRC} block-A (deviceCount==0 guard) regex did not match"
            " — skipping that removal",
            file=sys.stderr,
        )
    else:
        ctor_patched = ctor_after_a
        print(f"{CTOR_SRC}: removed rtlsdr_get_device_count()==0 guard (block A).")

    # Block B: remove "if (deviceIndex >= deviceCount) { throw ...; }"
    block_b = re.compile(
        r"[ \t]+if \(deviceIndex >= deviceCount\) \{[^}]*\}\n",
        re.DOTALL,
    )
    ctor_after_b = block_b.sub("", ctor_patched, count=1)
    if ctor_after_b == ctor_patched:
        print(
            f"WARNING: {CTOR_SRC} block-B (deviceIndex>=deviceCount) regex did not match"
            " — skipping that removal",
            file=sys.stderr,
        )
    else:
        ctor_patched = ctor_after_b
        print(f"{CTOR_SRC}: removed deviceIndex>=deviceCount bounds check (block B).")

    with open(CTOR_SRC, "w", encoding="utf-8") as f:
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
