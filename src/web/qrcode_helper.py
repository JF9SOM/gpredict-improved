"""
QR コード生成ヘルパー

LAN 内アクセス URL を QR コードにして PNG バイト列として返す。
Qt6 UI のステータスバーボタンやダイアログで表示する用途を想定している。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import qrcode
import qrcode.image.base


def generate_qr_png(url: str, box_size: int = 10, border: int = 4) -> bytes:
    """
    URL の QR コードを PNG バイト列として生成する。

    Args:
        url:      エンコードする URL 文字列
        box_size: 1 セルのピクセルサイズ（デフォルト 10）
        border:   周囲の余白セル数（デフォルト 4）

    Returns:
        PNG 形式のバイト列
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
    URL の QR コードを PNG ファイルとして保存する。

    Args:
        url:      エンコードする URL 文字列
        path:     保存先のファイルパス
        box_size: 1 セルのピクセルサイズ
        border:   周囲の余白セル数
    """
    png_bytes = generate_qr_png(url, box_size=box_size, border=border)
    path.write_bytes(png_bytes)
