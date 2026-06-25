"""
Extract SoapySDR DLLs and Python bindings from conda-forge packages.

Usage (PowerShell):
    python scripts/extract_soapy_conda.py

Reads all *.conda and *.tar.bz2 files in the current directory and extracts:
  - Device-module DLLs   -> soapy-win64/modules/
  - Python binding files -> soapy-win64/python/
  - Core DLLs            -> soapy-win64/bin/
"""

import bz2
import glob
import io
import os
import tarfile
import zipfile

import zstandard


def extract_conda(fname: str, bin_dir: str, py_dir: str, mod_dir: str) -> None:
    if fname.endswith(".conda"):
        with zipfile.ZipFile(fname) as z:
            pkg_name = next(n for n in z.namelist() if n.startswith("pkg-"))
            data = z.read(pkg_name)
            dctx = zstandard.ZstdDecompressor()
            with tarfile.open(fileobj=io.BytesIO(dctx.decompress(data))) as tf:
                _extract_members(tf, bin_dir, py_dir, mod_dir)
        return
    else:  # .tar.bz2
        with (
            open(fname, "rb") as f,
            tarfile.open(fileobj=io.BytesIO(bz2.decompress(f.read()))) as tf,
        ):
            _extract_members(tf, bin_dir, py_dir, mod_dir)
        return


def _extract_members(tf: tarfile.TarFile, bin_dir: str, py_dir: str, mod_dir: str) -> None:
    for m in tf.getmembers():
        name = m.name
        if not (name.endswith(".dll") or name.endswith(".pyd") or name.endswith(".py")):
            continue
        fobj = tf.extractfile(m)
        if not fobj:
            continue
        base = os.path.basename(name)
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
            continue
        with open(dest, "wb") as out:
            out.write(fobj.read())
        print(f"  -> {dest}")
        fobj.close()


for fname in glob.glob("*.conda") + glob.glob("*.tar.bz2"):
    print(f"Extracting {fname}")
    extract_conda(fname, "soapy-win64/bin", "soapy-win64/python", "soapy-win64/modules")
