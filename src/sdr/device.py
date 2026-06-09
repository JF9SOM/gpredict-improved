"""
SoapySDR device abstraction.

SdrDeviceInfo  — Enumerated device descriptor (driver, label, serial, etc.)
SdrDevice      — Thin wrapper around a SoapySDR.Device instance.

All public methods are thread-safe (protected by an internal lock).
When SoapySDR is not installed, SdrDevice.enumerate() returns [] and
any instantiation raises RuntimeError so callers can degrade gracefully.

USB fallback:
  When SoapySDR is absent, enumerate_usb() uses pyusb to scan for known
  SDR VID/PID pairs and returns a list of SdrDeviceInfo with driver=None.
  This is used by the SDR Device Installation dialog to identify devices
  even before the driver is installed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import SoapySDR as _soapy_probe  # noqa: F401

    SOAPY_AVAILABLE: bool = True
except Exception:
    SOAPY_AVAILABLE = False

try:
    import usb.core as _usb_probe  # noqa: F401

    PYUSB_AVAILABLE: bool = True
except Exception:
    PYUSB_AVAILABLE = False

# Known SDR device VID/PID pairs for USB fallback detection
_KNOWN_USB_DEVICES: list[tuple[int, int, str, str]] = [
    (0x0BDA, 0x2838, "RTL-SDR V3 / Blog V3", "SoapyRTLSDR"),
    (0x0BDA, 0x2832, "RTL-SDR Blog V4", "SoapyRTLSDR"),
    (0x0BDA, 0x2837, "RTL-SDR (generic)", "SoapyRTLSDR"),
    (0x1D50, 0x6089, "HackRF One", "SoapyHackRF"),
    (0x1D50, 0x60A1, "AirSpy", "SoapyAirspy"),
    (0x1D50, 0x60A0, "AirSpy Mini", "SoapyAirspy"),
    (0x1DF7, 0x2500, "SDRplay RSP1", "SoapySDRPlay"),
    (0x1DF7, 0x3000, "SDRplay RSP1A", "SoapySDRPlay"),
]


@dataclass
class SdrDeviceInfo:
    """Descriptor returned by SdrDevice.enumerate()."""

    driver: str | None  # SoapySDR driver name, e.g. "rtlsdr", "hackrf"
    label: str  # Human-readable name
    serial: str  # Serial number or empty string
    hardware: str  # Hardware revision string
    args: dict[str, str] = field(default_factory=dict)  # Raw SoapySDR kwargs
    vid: int = 0  # USB VID (USB fallback only)
    pid: int = 0  # USB PID (USB fallback only)
    soapy_module: str = ""  # Suggested SoapySDR module name for installation

    @property
    def display_name(self) -> str:
        """Short name for UI dropdowns."""
        if self.serial:
            return f"{self.label} #{self.serial}"
        return self.label


# SoapySDR drivers that expose non-hardware devices (audio cards, test sinks,
# network proxies).  These are excluded from the Rig Settings SDR device list
# so users only see real RF receivers (RTL-SDR, HackRF, AirSpy, etc.).
_NON_SDR_DRIVERS: frozenset[str] = frozenset({"audio", "null", "remote", "mircsdr"})


class SdrDevice:
    """
    Wrapper around a SoapySDR.Device.

    Instantiate with an SdrDeviceInfo (from enumerate()) or a raw kwargs dict.
    Call open() before streaming, close() when done.
    """

    def __init__(self, info: SdrDeviceInfo) -> None:
        if not SOAPY_AVAILABLE:
            raise RuntimeError(
                "SoapySDR is not installed. Install python3-soapysdr to enable SDR support."
            )
        self._info = info
        self._dev: Any = None
        self._stream: Any = None
        self._lock = threading.Lock()
        self._sample_rate: float = 2.4e6
        self._center_freq: float = 435.0e6
        self._bandwidth: float = 0.0  # 0 = auto
        self._gain_mode: str = "auto"  # "auto" or "manual"
        self._gain_db: float = 40.0
        self._ppm: float = 0.0
        self._bias_tee: bool = False

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    @classmethod
    def enumerate(cls) -> list[SdrDeviceInfo]:
        """Return SoapySDR-visible hardware SDR devices (audio devices excluded)."""
        if not SOAPY_AVAILABLE:
            return []
        try:
            import SoapySDR

            results: list[SdrDeviceInfo] = []
            for kw in SoapySDR.Device.enumerate():
                d = dict(kw)  # SoapySDRKwargs has no .get(); convert first
                driver = str(d.get("driver") or "")
                # Skip non-hardware drivers (audio, null, remote, etc.)
                if driver.lower() in _NON_SDR_DRIVERS:
                    continue
                label = str(d.get("label") or d.get("device") or driver)
                serial = str(d.get("serial") or "")
                hardware = str(d.get("hardware") or "")
                results.append(
                    SdrDeviceInfo(
                        driver=driver,
                        label=label,
                        serial=serial,
                        hardware=hardware,
                        args=dict(kw),
                    )
                )
            return results
        except Exception:
            logger.exception("SoapySDR enumerate failed")
            return []

    @classmethod
    def enumerate_usb(cls) -> list[SdrDeviceInfo]:
        """
        Enumerate connected SDR devices via USB VID/PID without SoapySDR.

        Tries pyusb first; falls back to Linux sysfs (/sys/bus/usb/devices/)
        when pyusb is not installed.  Used by the SDR Device Installation
        dialog to identify devices before the driver is installed.
        """
        if PYUSB_AVAILABLE:
            return cls._enumerate_usb_pyusb()
        return cls._enumerate_usb_sysfs()

    @classmethod
    def _enumerate_usb_pyusb(cls) -> list[SdrDeviceInfo]:
        """USB scan via pyusb."""
        try:
            import usb.core

            results: list[SdrDeviceInfo] = []
            for vid, pid, label, module in _KNOWN_USB_DEVICES:
                devs = list(usb.core.find(idVendor=vid, idProduct=pid, find_all=True) or [])
                for _ in devs:
                    results.append(
                        SdrDeviceInfo(
                            driver=None,
                            label=label,
                            serial="",
                            hardware="",
                            vid=vid,
                            pid=pid,
                            soapy_module=module,
                        )
                    )
            return results
        except Exception:
            logger.exception("USB enumeration (pyusb) failed")
            return []

    @classmethod
    def _enumerate_usb_sysfs(cls) -> list[SdrDeviceInfo]:
        """USB scan via Linux sysfs — no extra packages required."""
        import sys

        if sys.platform != "linux":
            return []
        try:
            from pathlib import Path

            known = {(vid, pid): (label, module) for vid, pid, label, module in _KNOWN_USB_DEVICES}
            results: list[SdrDeviceInfo] = []
            sysfs = Path("/sys/bus/usb/devices")
            if not sysfs.exists():
                return []
            for dev_path in sysfs.iterdir():
                vid_file = dev_path / "idVendor"
                pid_file = dev_path / "idProduct"
                if not vid_file.exists() or not pid_file.exists():
                    continue
                try:
                    vid = int(vid_file.read_text().strip(), 16)
                    pid = int(pid_file.read_text().strip(), 16)
                except ValueError:
                    continue
                if (vid, pid) in known:
                    label, module = known[(vid, pid)]
                    results.append(
                        SdrDeviceInfo(
                            driver=None,
                            label=label,
                            serial="",
                            hardware="",
                            vid=vid,
                            pid=pid,
                            soapy_module=module,
                        )
                    )
            return results
        except Exception:
            logger.exception("USB enumeration (sysfs) failed")
            return []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def info(self) -> SdrDeviceInfo:
        return self._info

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @property
    def center_freq(self) -> float:
        return self._center_freq

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """Open the device. Returns True on success."""
        import SoapySDR

        with self._lock:
            if self._dev is not None:
                return True
            try:
                self._dev = SoapySDR.Device(self._info.args)
                self._apply_settings()
                logger.info("SDR opened: %s", self._info.display_name)
                return True
            except Exception:
                logger.exception("Failed to open SDR device %s", self._info.display_name)
                self._dev = None
                return False

    def close(self) -> None:
        """Close the device and release resources."""
        with self._lock:
            self._stop_stream_locked()
            if self._dev is not None:
                self._dev = None
                logger.info("SDR closed: %s", self._info.display_name)

    # ------------------------------------------------------------------
    # Stream control
    # ------------------------------------------------------------------

    def start_stream(self, mtu: int = 1024) -> bool:
        """Activate the RX stream. Returns True on success."""
        import SoapySDR

        with self._lock:
            if self._dev is None:
                return False
            if self._stream is not None:
                return True
            try:
                self._stream = self._dev.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
                self._dev.activateStream(self._stream)
                return True
            except Exception:
                logger.exception("Failed to start SDR stream")
                self._stream = None
                return False

    def stop_stream(self) -> None:
        """Deactivate and close the RX stream."""
        with self._lock:
            self._stop_stream_locked()

    def read_samples(self, num_samples: int = 1024) -> np.ndarray | None:
        """
        Read num_samples complex64 samples.

        Returns None on timeout or error.  Non-blocking: uses a 50 ms timeout
        so the pipeline thread can check a stop flag between reads.
        """

        if self._stream is None or self._dev is None:
            return None
        buf = np.zeros(num_samples, dtype=np.complex64)
        try:
            sr = self._dev.readStream(self._stream, [buf], num_samples, timeoutUs=50_000)
            if sr.ret < 0:
                return None
            if sr.ret < num_samples:
                return buf[: sr.ret]
            return buf
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_sample_rate(self, rate_hz: float) -> bool:
        """Set the ADC sample rate in Hz."""
        with self._lock:
            self._sample_rate = rate_hz
            if self._dev is None:
                return True
            try:
                self._dev.setSampleRate(0, 0, rate_hz)  # direction=RX, channel=0
                return True
            except Exception:
                logger.exception("set_sample_rate failed")
                return False

    def set_center_freq(self, freq_hz: float) -> bool:
        """Tune the center frequency in Hz."""
        with self._lock:
            self._center_freq = freq_hz
            if self._dev is None:
                return True
            try:
                self._dev.setFrequency(0, 0, freq_hz)
                return True
            except Exception:
                logger.exception("set_center_freq failed")
                return False

    def set_bandwidth(self, bw_hz: float) -> bool:
        """Set the IF bandwidth in Hz (0 = automatic)."""
        with self._lock:
            self._bandwidth = bw_hz
            if self._dev is None:
                return True
            if bw_hz <= 0:
                return True
            try:
                self._dev.setBandwidth(0, 0, bw_hz)
                return True
            except Exception:
                logger.exception("set_bandwidth failed")
                return False

    def set_gain_auto(self) -> bool:
        """Enable automatic gain control."""
        with self._lock:
            self._gain_mode = "auto"
            if self._dev is None:
                return True
            try:
                self._dev.setGainMode(0, 0, True)
                return True
            except Exception:
                return False

    def set_gain_db(self, gain_db: float) -> bool:
        """Set manual gain in dB."""
        with self._lock:
            self._gain_mode = "manual"
            self._gain_db = gain_db
            if self._dev is None:
                return True
            try:
                self._dev.setGainMode(0, 0, False)
                self._dev.setGain(0, 0, gain_db)
                return True
            except Exception:
                logger.exception("set_gain_db failed")
                return False

    def set_bias_tee(self, enabled: bool) -> bool:
        """Enable or disable the Bias-T power supply on the antenna port.

        Bias-T injects DC voltage into the coax to power an external LNA or
        active antenna.  The SoapySDR writeSetting key and value format differ
        by driver:
          - RTL-SDR (soapyrtlsdr): key="biastee",  value="1" / "0"
          - HackRF  (soapyhackrf): key="bias_tx",  value="true" / "false"
          - AirSpy  (soapyairspy): key="biastee",  value="true" / "false"
        We try all known combinations in order and return True on first success.
        """
        with self._lock:
            self._bias_tee = enabled
            if self._dev is None:
                return True
            # (key, value_on, value_off) — driver-specific combinations
            candidates: list[tuple[str, str, str]] = [
                ("biastee", "1", "0"),  # RTL-SDR
                ("bias_tx", "true", "false"),  # HackRF
                ("biastee", "true", "false"),  # AirSpy / generic
                ("BiasT", "true", "false"),  # some other drivers
            ]
            for key, val_on, val_off in candidates:
                value = val_on if enabled else val_off
                try:
                    self._dev.writeSetting(key, value)
                    logger.info(
                        "Bias-T %s (key='%s', value='%s')",
                        "ON" if enabled else "OFF",
                        key,
                        value,
                    )
                    return True
                except Exception:
                    pass
            logger.warning("Bias-T: no supported writeSetting key found for this device")
            return False

    def set_ppm(self, ppm: float) -> bool:
        """Set frequency correction in parts per million."""
        with self._lock:
            self._ppm = ppm
            if self._dev is None:
                return True
            try:
                self._dev.setFrequencyComponent(0, 0, "CORR", ppm)
                return True
            except Exception:
                # Not all drivers support PPM correction via this call
                return False

    def get_sample_rates(self) -> list[float]:
        """Return list of supported sample rates (Hz)."""
        if not SOAPY_AVAILABLE or self._dev is None:
            return [250e3, 1.0e6, 1.4e6, 1.8e6, 2.0e6, 2.4e6, 3.2e6]
        try:
            ranges = self._dev.getSampleRateRange(0, 0)
            # Return a curated set within the supported range
            candidates = [250e3, 500e3, 1.0e6, 1.4e6, 1.8e6, 2.0e6, 2.4e6, 3.2e6]
            lo = ranges[0].minimum() if ranges else 0
            hi = ranges[0].maximum() if ranges else 4e6
            return [r for r in candidates if lo <= r <= hi]
        except Exception:
            return [250e3, 1.0e6, 2.4e6]

    def get_gain_range(self) -> tuple[float, float]:
        """Return (min_db, max_db) for the overall gain element."""
        if not SOAPY_AVAILABLE or self._dev is None:
            return (0.0, 50.0)
        try:
            r = self._dev.getGainRange(0, 0)
            return (r.minimum(), r.maximum())
        except Exception:
            return (0.0, 50.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_settings(self) -> None:
        """Push stored settings to the freshly opened device."""
        import SoapySDR

        if self._dev is None:
            return
        self._dev.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, self._sample_rate)
        self._dev.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, self._center_freq)
        if self._bandwidth > 0:
            self._dev.setBandwidth(SoapySDR.SOAPY_SDR_RX, 0, self._bandwidth)
        if self._gain_mode == "auto":
            self._dev.setGainMode(SoapySDR.SOAPY_SDR_RX, 0, True)
        else:
            self._dev.setGainMode(SoapySDR.SOAPY_SDR_RX, 0, False)
            self._dev.setGain(SoapySDR.SOAPY_SDR_RX, 0, self._gain_db)
        if self._ppm != 0.0:
            with contextlib.suppress(Exception):
                self._dev.setFrequencyComponent(SoapySDR.SOAPY_SDR_RX, 0, "CORR", self._ppm)
        if self._bias_tee:
            for key, val_on, _val_off in [
                ("biastee", "1", "0"),
                ("bias_tx", "true", "false"),
                ("biastee", "true", "false"),
                ("BiasT", "true", "false"),
            ]:
                try:
                    self._dev.writeSetting(key, val_on)
                    break
                except Exception:
                    pass

    def _stop_stream_locked(self) -> None:
        """Stop and release the stream. Must be called with _lock held."""
        if self._stream is not None and self._dev is not None:
            try:
                self._dev.deactivateStream(self._stream)
                self._dev.closeStream(self._stream)
            except Exception:
                pass
            self._stream = None


import contextlib  # noqa: E402  (placed here to avoid top-level cycle)
