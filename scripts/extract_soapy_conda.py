"""
Extract SoapySDR DLLs and Python bindings from conda-forge packages.

Usage (PowerShell):
    python scripts/extract_soapy_conda.py

Reads all *.conda and *.tar.bz2 files in the current directory and extracts:
  - Device-module DLLs   -> soapy-win64/modules/
  - Python binding files -> soapy-win64/python/
  - Core DLLs            -> soapy-win64/bin/
  - C++ SDK (headers, .lib, cmake) from the SoapySDR *core* package only
    -> soapy-win64/sdk/include/ and soapy-win64/sdk/lib/
    Used by the "Build SoapyRTLSDR" CI step so that rtlsdrSupport.dll is
    compiled against the exact same SoapySDR ABI as the bundled SoapySDR.dll.
"""

import bz2
import glob
import io
import os
import tarfile
import zipfile

import zstandard


def extract_conda(
    fname: str,
    bin_dir: str,
    py_dir: str,
    mod_dir: str,
    sdk_dir: str | None = None,
) -> None:
    if fname.endswith(".conda"):
        with zipfile.ZipFile(fname) as z:
            pkg_name = next(n for n in z.namelist() if n.startswith("pkg-"))
            data = z.read(pkg_name)
            dctx = zstandard.ZstdDecompressor()
            with tarfile.open(fileobj=io.BytesIO(dctx.decompress(data))) as tf:
                _extract_members(tf, bin_dir, py_dir, mod_dir, sdk_dir)
        return
    else:  # .tar.bz2
        with (
            open(fname, "rb") as f,
            tarfile.open(fileobj=io.BytesIO(bz2.decompress(f.read()))) as tf,
        ):
            _extract_members(tf, bin_dir, py_dir, mod_dir, sdk_dir)
        return


def _extract_members(
    tf: tarfile.TarFile,
    bin_dir: str,
    py_dir: str,
    mod_dir: str,
    sdk_dir: str | None = None,
) -> None:
    for m in tf.getmembers():
        name = m.name
        fobj = tf.extractfile(m)
        if not fobj:
            continue
        base = os.path.basename(name)

        # ── SDK extraction (headers, import libs, cmake configs) ──────────────
        # Only processed when sdk_dir is set (SoapySDR core package only).
        # Extracts Library/include/ → sdk_dir/include/
        #          Library/lib/     → sdk_dir/lib/   (skip .dll, keep .lib/.cmake/.pc)
        # This ensures rtlsdrSupport.dll is built against the same SOAPY_SDR_ABI_VERSION
        # as the bundled SoapySDR.dll, preventing "failed ABI check" at load time.
        if sdk_dir is not None and not name.endswith("/"):
            if "Library/include/" in name:
                rel = name.split("Library/include/", 1)[1]
                if rel:
                    dest = os.path.join(sdk_dir, "include", rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as out:
                        out.write(fobj.read())
                    print(f"  [sdk-inc] -> {dest}")
                fobj.close()
                continue
            if "Library/lib/" in name and (
                name.endswith(".lib")
                or name.endswith(".cmake")
                or name.endswith(".pc")
                or name.endswith(".prl")
            ):
                rel = name.split("Library/lib/", 1)[1]
                if rel and not rel.endswith("/"):
                    dest = os.path.join(sdk_dir, "lib", rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as out:
                        out.write(fobj.read())
                    print(f"  [sdk-lib] -> {dest}")
                fobj.close()
                continue
        # ──────────────────────────────────────────────────────────────────────

        if not (name.endswith(".dll") or name.endswith(".pyd") or name.endswith(".py")):
            fobj.close()
            continue

        if "modules" in name and name.endswith(".dll"):
            # Skip SoapyRTLSDR — we build our own WinUSB-patched version from
            # Osmocom source in the CI "Build SoapyRTLSDR" step.  The conda-forge
            # rtlsdrSupport.dll would create a second factory inside SoapySDR and
            # cause Device::make() to fail with "no match".
            if base.lower() == "rtlsdrsupport.dll":
                print(f"  [skip] {base} (using custom WinUSB-patched build)")
                fobj.close()
                continue
            dest = os.path.join(mod_dir, base)
        elif name.endswith(".pyd") or (name.endswith(".py") and "SoapySDR" in base):
            dest = os.path.join(py_dir, base)
        elif name.endswith(".dll"):
            dest = os.path.join(bin_dir, base)
        else:
            fobj.close()
            continue
        with open(dest, "wb") as out:
            out.write(fobj.read())
        print(f"  -> {dest}")
        fobj.close()


os.makedirs("soapy-win64/sdk/include", exist_ok=True)
os.makedirs("soapy-win64/sdk/lib", exist_ok=True)

for fname in glob.glob("*.conda") + glob.glob("*.tar.bz2"):
    print(f"Extracting {fname}")
    # Extract the C++ SDK only from the SoapySDR *core* package (not module packages).
    # The core package name starts with "soapysdr-" but does NOT contain "module".
    # This ensures the SDK headers/lib match the bundled SoapySDR.dll exactly.
    basename = os.path.basename(fname)
    is_soapy_core = basename.startswith("soapysdr-") and "module" not in basename
    extract_conda(
        fname,
        "soapy-win64/bin",
        "soapy-win64/python",
        "soapy-win64/modules",
        sdk_dir="soapy-win64/sdk" if is_soapy_core else None,
    )
