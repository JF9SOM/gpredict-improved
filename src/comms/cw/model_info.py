"""CW decoder model path management and availability checks.

Model source: DeepCW (e04/web-deep-cw-decoder), ONNX format.
Downloaded directly from e04's GitHub Pages — no CI build required.

Detection priority:
  1. User-installed  ~/.local/share/fbsat59/cwmodel/  (Linux)
  2. Bundled         _MEIPASS/cwmodel/                (PyInstaller)
"""

from __future__ import annotations

import sys
from pathlib import Path

import platformdirs

# ---------------------------------------------------------------------------
# Model file names (local storage)
# ---------------------------------------------------------------------------

MODEL_FILES: dict[str, str] = {
    "en": "model_en.onnx",
    "ja": "model_ja.onnx",
    "detect": "detect_cw.onnx",
}

# Download URLs — ONNX binaries served from e04's GitHub Pages
_BASE_URL = "https://e04.github.io/web-deep-cw-decoder/dist/models"
MODEL_URLS: dict[str, str] = {
    "en": f"{_BASE_URL}/en/39578E22-27CE-4AFB-989F-450345767A53",
    "ja": f"{_BASE_URL}/ja/A960AA1B-FFD3-4795-A881-484F4EEB0455",
    "detect": f"{_BASE_URL}/detect_cw/88C0EAD8-52C6-460C-9B9F-EE6CB56221F3",
}


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def get_user_cw_model_dir() -> Path:
    """Return the user data directory for CW model files."""
    return Path(platformdirs.user_data_dir("fbsat59")) / "cwmodel"


def find_model(name: str) -> Path | None:
    """Return the path to the named model file, or None if not found.

    Checks user directory first, then PyInstaller bundle.
    """
    filename = MODEL_FILES.get(name)
    if not filename:
        return None

    # 1. User-installed
    user_path = get_user_cw_model_dir() / filename
    if user_path.exists():
        return user_path

    # 2. PyInstaller bundle
    if getattr(sys, "frozen", False):
        bundle_path = Path(getattr(sys, "_MEIPASS", "")) / "cwmodel" / filename
        if bundle_path.exists():
            return bundle_path

    return None


def is_onnxruntime_available() -> bool:
    """Return True if onnxruntime can be imported."""
    try:
        import onnxruntime  # noqa: F401

        return True
    except ImportError:
        return False


def all_models_available() -> bool:
    """Return True if onnxruntime and all required model files are present."""
    if not is_onnxruntime_available():
        return False
    return all(find_model(name) is not None for name in ("en", "detect"))
