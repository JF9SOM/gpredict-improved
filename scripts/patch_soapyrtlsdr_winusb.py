"""
Patch SoapyRTLSDR/Registration.cpp to replace findRTLSDR() with a
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

# Locate findRTLSDR using brace-counting (regex \n} matches inner braces)
marker = "findRTLSDR"
idx = content.find(marker)
if idx == -1:
    print("ERROR: findRTLSDR not found in Registration.cpp", file=sys.stderr)
    print(content, file=sys.stderr)
    sys.exit(1)

# Walk back to the start of the return-type line
func_start = content.rfind("\n", 0, idx) + 1

# Find the opening brace of the function body
brace_open = content.find("{", idx)

# Count braces to find the matching closing brace
depth = 0
pos = brace_open
while pos < len(content):
    if content[pos] == "{":
        depth += 1
    elif content[pos] == "}":
        depth -= 1
        if depth == 0:
            func_end = pos + 1
            break
    pos += 1

print("Replacing:", file=sys.stderr)
print(content[func_start : func_start + 80], file=sys.stderr)

NEW_FUNC = """\
SoapySDR::KwargsList findRTLSDR(const SoapySDR::Kwargs &args)
{
    SoapySDR::KwargsList results;
    const int count = rtlsdr_get_device_count();
    for (int i = 0; i < count; i++)
    {
        char manufact[256] = {}, product[256] = {}, serial[256] = {};
        // WinUSB cannot provide USB string descriptors; ignore return value.
        rtlsdr_get_device_usb_strings(i, manufact, product, serial);
        // Filter by device_index when caller specifies one.
        if (args.count("device_index") != 0 &&
            args.at("device_index") != std::to_string(i)) continue;
        // Filter by serial only when we actually read a non-empty serial.
        if (args.count("serial") != 0 && serial[0] != '\\0' &&
            args.at("serial") != serial) continue;
        SoapySDR::Kwargs devInfo;
        devInfo["device_index"] = std::to_string(i);
        devInfo["serial"]       = serial;
        devInfo["product"]      = product;
        devInfo["manufacturer"] = manufact;
        devInfo["label"]        = std::string(rtlsdr_get_device_name(i))
                                  + " :: " + serial;
        results.push_back(devInfo);
    }
    return results;
}"""

patched = content[:func_start] + NEW_FUNC + content[func_end:]
with open("SoapyRTLSDR/Registration.cpp", "w") as f:
    f.write(patched)
print("findRTLSDR replaced with WinUSB-compatible version.")
