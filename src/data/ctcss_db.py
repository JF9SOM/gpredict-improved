"""CTCSS tone database indexed by NORAD ID."""

from __future__ import annotations

# Source: https://www.amsat.org/live-fm-satellites/ (Updated May 14, 2026)
CTCSS_DB: dict[int, dict[str, float | None]] = {
    25544: {"tone_hz": 67.0, "activation_hz": None},  # ISS
    27607: {"tone_hz": 67.0, "activation_hz": 74.4},  # SO-50 (SaudiSat-1C)
    40931: {"tone_hz": 88.5, "activation_hz": None},  # IO-86 (LAPAN-A2)
    42017: {"tone_hz": 67.0, "activation_hz": None},  # AO-91 (RadFxSat/Fox-1B)
    57167: {"tone_hz": 141.3, "activation_hz": None},  # PO-101 (Diwata-2)
    61781: {"tone_hz": 67.0, "activation_hz": None},  # AO-123 (ASRTU-1)
    67291: {"tone_hz": 67.0, "activation_hz": None},  # RS-95S (QMR-KWT 2)
}


def get_ctcss(norad_cat_id: int) -> dict[str, float | None] | None:
    """Return CTCSS info for a satellite, or None if not in database."""
    return CTCSS_DB.get(norad_cat_id)
