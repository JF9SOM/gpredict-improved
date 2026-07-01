"""Shared soundcard access for the Communications tabs.

CW Decoder, SSTV/SSDV, FT4, Q65, and APRS (Direwolf) can all be open at the
same time and all read from — or write to — the single soundcard device
configured in Rig Settings > Sound Card. Two tabs each calling
``sounddevice.InputStream()`` directly on the same device index either raise
a "device busy" error (ALSA ``hw:`` devices reject a second open) or silently
starve each other of samples.

This module gives every consumer a single point of contact for that shared
hardware:

  - RX (input) is opened once per device and fanned out to every subscriber
    (pub/sub), since multiple decoders reading the same incoming audio is a
    legitimate use case (e.g. CW Decoder and SSTV open at the same time).
  - TX (output) is exclusively locked to one owner at a time, since two tabs
    transmitting simultaneously would just garble the audio on air.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

try:
    from scipy import signal as sp_signal

    _SCIPY_AVAILABLE: bool = True
except ImportError:
    sp_signal = None
    _SCIPY_AVAILABLE = False

# Every shared input stream is opened at this rate; each subscriber's audio
# is resampled from here to whatever rate it asked for.
_HW_SAMPLE_RATE = 48_000

AudioCallback = Callable[[NDArray[np.float32]], None]


def _resample(chunk: NDArray[np.float32], src_rate: int, dst_rate: int) -> NDArray[np.float32]:
    """Resample a mono float32 chunk from src_rate to dst_rate."""
    if src_rate == dst_rate or len(chunk) == 0:
        return chunk
    if src_rate % dst_rate == 0:
        return chunk[:: src_rate // dst_rate]
    if _SCIPY_AVAILABLE:
        g = np.gcd(src_rate, dst_rate)
        resampled = sp_signal.resample_poly(chunk, dst_rate // g, src_rate // g)
        return cast(NDArray[np.float32], resampled.astype(np.float32))
    n_out = max(1, round(len(chunk) * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=len(chunk), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, chunk).astype(np.float32)


class _SharedInputStream:
    """A single real sounddevice.InputStream, fanned out to N subscribers."""

    def __init__(self, device: int | None) -> None:
        self._device = device
        self._stream: Any = None
        self._subscribers: dict[str, tuple[int, AudioCallback]] = {}
        self._lock = threading.Lock()

    def add_subscriber(self, owner: str, samplerate: int, callback: AudioCallback) -> None:
        with self._lock:
            self._subscribers[owner] = (samplerate, callback)
            if self._stream is None:
                self._open()

    def remove_subscriber(self, owner: str) -> bool:
        """Unsubscribe `owner`. Returns True once no subscribers remain."""
        with self._lock:
            self._subscribers.pop(owner, None)
            if not self._subscribers:
                self._close()
                return True
            return False

    def _open(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=_HW_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self._device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    def _on_audio(
        self, indata: NDArray[np.float32], frames: int, time_info: Any, status: Any
    ) -> None:
        chunk = indata[:, 0].copy()
        with self._lock:
            subs = list(self._subscribers.values())
        for samplerate, callback in subs:
            with contextlib.suppress(Exception):
                callback(_resample(chunk, _HW_SAMPLE_RATE, samplerate))


class AudioDeviceManager:
    """Process-wide coordinator for shared RX streams and exclusive TX locks."""

    _instance: AudioDeviceManager | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._inputs: dict[int, _SharedInputStream] = {}
        self._inputs_lock = threading.Lock()
        self._tx_owners: dict[int, str] = {}
        self._tx_lock = threading.Lock()

    @classmethod
    def instance(cls) -> AudioDeviceManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def _key(device: int | None) -> int:
        return -1 if device is None else device

    # ------------------------------------------------------------------ #
    # RX — shared input (pub/sub)
    # ------------------------------------------------------------------ #

    def acquire_input(
        self, owner: str, device: int | None, samplerate: int, callback: AudioCallback
    ) -> None:
        """Subscribe `owner` to audio from `device`, delivered at `samplerate`.

        Opens the underlying hardware stream on the first subscriber; later
        subscribers on the same device share it (resampled as needed).
        Calling this again for an `owner` that is already subscribed just
        updates its callback/samplerate.
        """
        key = self._key(device)
        with self._inputs_lock:
            stream = self._inputs.get(key)
            if stream is None:
                stream = _SharedInputStream(device)
                self._inputs[key] = stream
        stream.add_subscriber(owner, samplerate, callback)

    def release_input(self, owner: str, device: int | None) -> None:
        """Unsubscribe `owner` from `device`, closing the hardware stream if
        `owner` was the last subscriber."""
        key = self._key(device)
        with self._inputs_lock:
            stream = self._inputs.get(key)
            if stream is None:
                return
            if stream.remove_subscriber(owner):
                del self._inputs[key]

    # ------------------------------------------------------------------ #
    # TX — exclusive output lock
    # ------------------------------------------------------------------ #

    def acquire_output(self, owner: str, device: int | None) -> bool:
        """Try to claim exclusive use of the output device for `owner`.

        Returns True if claimed (or already held by `owner`), False if a
        different owner currently holds it.
        """
        key = self._key(device)
        with self._tx_lock:
            current = self._tx_owners.get(key)
            if current is not None and current != owner:
                return False
            self._tx_owners[key] = owner
            return True

    def release_output(self, owner: str, device: int | None) -> None:
        """Release `owner`'s exclusive claim on the output device, if held."""
        key = self._key(device)
        with self._tx_lock:
            if self._tx_owners.get(key) == owner:
                del self._tx_owners[key]

    def output_owner(self, device: int | None) -> str | None:
        """Return the name of the current TX owner of `device`, if any."""
        key = self._key(device)
        with self._tx_lock:
            return self._tx_owners.get(key)


def get_audio_device_manager() -> AudioDeviceManager:
    """Return the process-wide AudioDeviceManager singleton."""
    return AudioDeviceManager.instance()
