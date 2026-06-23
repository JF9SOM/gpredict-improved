"""
Patch SoapyRTLSDR/Registration.cpp: replace findRTLSDR() with a
WinUSB-compatible version that does not require rtlsdr_get_device_usb_strings()
to succeed.

WinUSB cannot read USB string descriptors via libusb, so the original
findRTLSDR() skips devices when that call fails, causing enumerate() to
return [] and make() to fail with "no match".

The replacement always includes devices by index.  USB strings are
best-effort; matching falls back to device_index when serial is empty.
"""

import sys

with open("SoapyRTLSDR/Registration.cpp", "r") as f:
    content = f.read()

# Find the DEFINITION of findRTLSDR (not the forward declaration).
# A definition has '{' before the next ';'; a forward decl has ';' first.
search_from = 0
func_start = -1
brace_open_abs = -1

while True:
    idx = content.find("findRTLSDR", search_from)
    if idx == -1:
        print("ERROR: findRTLSDR not found in Registration.cpp", file=sys.stderr)
        print(content[:500], file=sys.stderr)
        sys.exit(1)

    after = content[idx:]
    next_brace = after.find("{")
    next_semi  = after.find(";")

    if next_brace != -1 and (next_semi == -1 or next_brace < next_semi):
        # '{' comes before ';' → this is the function definition
        brace_open_abs = idx + next_brace
        func_start = content.rfind("\n", 0, idx) + 1
        break

    search_from = idx + 1  # forward declaration or other ref; keep searching

# Use brace-counting to find the matching closing brace of the function body
depth = 0
pos = brace_open_abs
while pos < len(content):
    if content[pos] == "{":
        depth += 1
    elif content[pos] == "}":
        depth -= 1
        if depth == 0:
            func_end = pos + 1
            break
    pos += 1

# Preserve 'static' keyword if the original used it (avoid linkage mismatch)
original_decl = content[func_start:brace_open_abs]
static_kw = "static " if "static" in original_decl else ""

print(f"Replacing findRTLSDR definition [{func_start}:{func_end}], "
      f"static={bool(static_kw)}", file=sys.stderr)
print(repr(content[func_start:func_start + 100]), file=sys.stderr)

NEW_FUNC = (
    f"{static_kw}SoapySDR::KwargsList findRTLSDR(const SoapySDR::Kwargs &args)\n"
    "{\n"
    "    SoapySDR::KwargsList results;\n"
    "    const int count = rtlsdr_get_device_count();\n"
    "    for (int i = 0; i < count; i++)\n"
    "    {\n"
    "        char manufact[256] = {}, product[256] = {}, serial[256] = {};\n"
    "        // WinUSB cannot provide USB string descriptors; ignore return value.\n"
    "        rtlsdr_get_device_usb_strings(i, manufact, product, serial);\n"
    "        // Filter by device_index when caller specifies one.\n"
    '        if (args.count("device_index") != 0 &&\n'
    '            args.at("device_index") != std::to_string(i)) continue;\n'
    "        // Filter by serial only when we actually read a non-empty serial.\n"
    '        if (args.count("serial") != 0 && serial[0] != \'\\0\' &&\n'
    '            args.at("serial") != serial) continue;\n'
    "        SoapySDR::Kwargs devInfo;\n"
    '        devInfo["device_index"] = std::to_string(i);\n'
    '        devInfo["serial"]       = serial;\n'
    '        devInfo["product"]      = product;\n'
    '        devInfo["manufacturer"] = manufact;\n'
    '        devInfo["label"]        = std::string(rtlsdr_get_device_name(i))\n'
    '                                  + " :: " + serial;\n'
    "        results.push_back(devInfo);\n"
    "    }\n"
    "    return results;\n"
    "}"
)

patched = content[:func_start] + NEW_FUNC + content[func_end:]
with open("SoapyRTLSDR/Registration.cpp", "w") as f:
    f.write(patched)
print("findRTLSDR replaced with WinUSB-compatible version.")
