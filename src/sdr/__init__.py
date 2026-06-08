"""
SDR (Software Defined Radio) subsystem.

Provides SoapySDR-based device access, I/Q pipeline, demodulators,
IQ recorder, and a plugin framework for future data-mode extensions.

SoapySDR is an optional system-level dependency.  When not installed,
SDR features are hidden from the UI (graceful degradation).
"""

from __future__ import annotations

try:
    import SoapySDR as _soapy_probe  # noqa: F401

    SOAPY_AVAILABLE: bool = True
except Exception as _e:
    import logging as _logging

    _logging.getLogger(__name__).warning("SoapySDR import failed: %s: %s", type(_e).__name__, _e)
    SOAPY_AVAILABLE = False
