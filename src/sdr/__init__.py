"""
SDR (Software Defined Radio) subsystem.

Provides SoapySDR-based device access, I/Q pipeline, demodulators,
IQ recorder, and a plugin framework for future data-mode extensions.

SoapySDR is an optional system-level dependency.  When not installed,
SDR features are hidden from the UI (graceful degradation).
"""

from __future__ import annotations

import importlib.util

SOAPY_AVAILABLE: bool = importlib.util.find_spec("SoapySDR") is not None
