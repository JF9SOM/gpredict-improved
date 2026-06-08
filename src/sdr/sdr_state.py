"""
Shared SDR state for the Web API.

SdrWebState is a thread-safe container written by the Qt UI/SDR threads and
read by the FastAPI (uvicorn) thread — mirroring the pattern used by RigWebState.
"""

from __future__ import annotations


class SdrWebState:
    """
    Shared mutable SDR state between the Qt UI thread and the FastAPI thread.

    All fields are plain Python scalars — the GIL guarantees atomic reads/writes.
    """

    def __init__(self) -> None:
        # Which rig slot the SDR occupies (1 or 2, None = not assigned)
        self.rig_slot: int | None = None

        # Connection / streaming state
        self.connected: bool = False
        self.streaming: bool = False

        # Device info
        self.device_label: str = ""
        self.driver: str = ""

        # Tuning
        self.center_freq_hz: float = 0.0
        self.sample_rate_hz: float = 2_400_000.0

        # Demodulator
        self.demod_mode: str = ""  # "NFM" / "USB" / "LSB" / "CW"
        self.audio_active: bool = False

        # IQ recorder
        self.recording: bool = False
        self.recording_file: str = ""
        self.recording_bytes: int = 0
        self.recording_elapsed_s: float = 0.0

        # Spectrum (latest FFT — updated ~10 fps)
        # List of (freq_hz, power_dbfs) pairs; empty when not streaming
        self.spectrum: list[tuple[float, float]] = []
