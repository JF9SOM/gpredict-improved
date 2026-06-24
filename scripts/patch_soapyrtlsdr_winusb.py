"""
Patch SoapyRTLSDR/Registration.cpp for WinUSB compatibility.

Root cause (confirmed by log analysis):
  makeRTLSDR() calls rtlsdr_get_device_count() as a bounds check before
  calling new SoapyRTLSDR(args).  rtlsdr_get_device_count() performs
  libusb_init(&ctx) + libusb_exit(ctx) with a private libusb context.
  Under Windows WinUSB, libusb_exit() resets the USB backend handle cache.
  The subsequent rtlsdr_open() inside the SoapyRTLSDR constructor then calls
  libusb_init(&dev->ctx) + libusb_get_device_list(), but WinUSB can no longer
  enumerate the device → "No RTL-SDR devices found!".
  Under libusbK this is not an issue (kernel-mode filter driver persists).

Patches applied (both in Registration.cpp):

  1. findRTLSDR  — return a single device_index=0 candidate without calling
                   any libusb/librtlsdr function.  This also fixes the
                   "4 phantom entries" in the SDR dropdown.

  2. makeRTLSDR  — skip rtlsdr_get_device_count() entirely.  rtlsdr_open()
                   inside the SoapyRTLSDR constructor handles out-of-range
                   indices with its own error path.  With no libusb init/exit
                   before rtlsdr_open(), WinUSB enumerates the device
                   successfully.
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
