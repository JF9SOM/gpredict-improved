"""FT4 6-second period scheduler.

FT4 divides UTC time into interleaved 6-second slots:
  even slots: floor(t/6) % 2 == 0  (0–6 s, 12–18 s, …)
  odd  slots: floor(t/6) % 2 == 1  (6–12 s, 18–24 s, …)

One station transmits in even slots, the other in odd slots.  The caller
decides which role to take via set_tx_even().
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal


class Ft4Scheduler(QObject):
    """Drives FT4 timing with 100 ms resolution.

    Signals:
        period_tick(is_tx, seconds_remaining):
            Fired ~10× per second.  is_tx reflects the current slot role.
        period_changed(is_tx):
            Fired once at each slot boundary (TX→RX or RX→TX).
        rx_period_ended():
            Fired at the end of an RX slot — the accumulation buffer is ready
            to be decoded.
    """

    period_tick: Signal = Signal(bool, float)  # (is_tx, seconds_remaining)
    period_changed: Signal = Signal(bool)  # (is_tx)
    rx_period_ended: Signal = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tx_even: bool = True
        self._running: bool = False
        self._prev_slot_num: int = -1
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start(self, tx_even: bool = True) -> None:
        """Start the scheduler.  tx_even=True: transmit in even 6-second slots."""
        self._tx_even = tx_even
        self._running = True
        self._prev_slot_num = -1
        self._timer.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        self._timer.stop()

    def set_tx_even(self, tx_even: bool) -> None:
        """Change the TX/RX assignment while running."""
        self._tx_even = tx_even

    @staticmethod
    def current_slot_info() -> tuple[bool, float]:
        """Return (is_even_slot, position_in_slot) for the current moment."""
        now = time.time()
        slot_num = int(now / 6.0)
        is_even = slot_num % 2 == 0
        pos = now % 6.0
        return is_even, pos

    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.time()
        slot_num = int(now / 6.0)
        is_even = slot_num % 2 == 0
        is_tx = is_even == self._tx_even
        pos = now % 6.0
        seconds_remaining = 6.0 - pos

        if self._prev_slot_num != slot_num and self._prev_slot_num >= 0:
            # Slot boundary crossed
            prev_is_even = self._prev_slot_num % 2 == 0
            prev_is_tx = prev_is_even == self._tx_even
            if prev_is_tx:
                # We just ended a TX slot — nothing extra needed
                pass
            else:
                # We just ended an RX slot — trigger decode
                self.rx_period_ended.emit()
            self.period_changed.emit(is_tx)

        self._prev_slot_num = slot_num
        self.period_tick.emit(is_tx, seconds_remaining)
