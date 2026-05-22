"""
QR code generation helper.

Encodes a LAN access URL as a QR code and returns it as PNG bytes.
Intended for display in Qt6 UI status bar buttons and dialogs.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import qrcode
import qrcode.image.base


def generate_qr_png(url: str, box_size: int = 10, border: int = 4) -> bytes:
    """
    Generate a QR code for the given URL and return it as PNG bytes.

    Args:
        url:      URL string to encode
        box_size: Pixel size per cell (default 10)
        border:   Number of quiet-zone cells around the code (default 4)

    Returns:
        PNG bytes
    """
    qr: Any = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_qr_png(url: str, path: Path, box_size: int = 10, border: int = 4) -> None:
    """
    Generate a QR code for the given URL and save it as a PNG file.

    Args:
        url:      URL string to encode
        path:     Destination file path
        box_size: Pixel size per cell
        border:   Number of quiet-zone cells around the code
    """
    png_bytes = generate_qr_png(url, box_size=box_size, border=border)
    path.write_bytes(png_bytes)
