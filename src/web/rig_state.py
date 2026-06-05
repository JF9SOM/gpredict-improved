"""
Shared rig/rotator state object for the web API.

RigWebState is a simple thread-safe container that the Qt UI thread writes
and the FastAPI (uvicorn) thread reads.  No locks are needed for the scalar
fields because Python's GIL makes individual attribute reads/writes atomic.
"""

from __future__ import annotations


class RigWebState:
    """Shared mutable state between the Qt UI thread and the FastAPI thread.

    The Qt main_window writes these fields every tick; the WebSocket endpoint
    reads them to push live data to the mobile browser.

    All fields are plain Python scalars — GIL guarantees atomic reads/writes.
    """

    def __init__(self) -> None:
        # Rig 1 Doppler control
        self.rig_engaged: bool = False  # True when Doppler correction is active
        self.rig_connected: bool = False  # True when rig is connected
        self.dl_hz: float | None = None  # Current Doppler-corrected downlink (Hz)
        self.ul_hz: float | None = None  # Current Doppler-corrected uplink (Hz)
        self.dl_doppler_hz: float | None = None  # Doppler shift applied to DL (Hz)
        self.ul_doppler_hz: float | None = None  # Doppler shift applied to UL (Hz)
        self.mode: str = ""  # Current mode string (FM / SSB / CW …)

        # Rotator
        self.rot_connected: bool = False  # True when rotator is connected
        self.rot_engaged: bool = False  # True when rotator tracking is active
        self.rot_az: float | None = None  # Current rotator azimuth (deg)
        self.rot_el: float | None = None  # Current rotator elevation (deg)

        # Toggle request flags — set by REST endpoint, cleared by Qt UI
        self.rig_toggle_requested: bool = False
        self.rot_toggle_requested: bool = False
