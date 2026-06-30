"""CW decoder model path management and availability checks.

Model source: DeepCW engine (e04/deepcw-engine), ONNX format.
Downloaded directly from GitHub raw content — no CI build required.

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

MODEL_FILE = "model.onnx"

# Download URL — raw content from e04/deepcw-engine main branch
MODEL_URL = "https://raw.githubusercontent.com/e04/deepcw-engine/main/model.onnx"


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def get_user_cw_model_dir() -> Path:
    """Return the user data directory for CW model files."""
    return Path(platformdirs.user_data_dir("fbsat59")) / "cwmodel"


def find_model() -> Path | None:
    """Return the path to model.onnx, or None if not found.

    Checks user directory first, then PyInstaller bundle.
    """
    # 1. User-installed
    user_path = get_user_cw_model_dir() / MODEL_FILE
    if user_path.exists():
        return user_path

    # 2. PyInstaller bundle
    if getattr(sys, "frozen", False):
        bundle_path = Path(getattr(sys, "_MEIPASS", "")) / "cwmodel" / MODEL_FILE
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


def is_ready() -> bool:
    """Return True if onnxruntime and model.onnx are both present."""
    return is_onnxruntime_available() and find_model() is not None
