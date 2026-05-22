"""
rig/controller.py のユニットテスト

Hamlib 未インストール環境（CI）でもすべてパスする。
ネットワーク接続不要（httpx をモック）。
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from rig.controller import (
    HAMLIB_AVAILABLE,
    FrequencyState,
    HamlibDirectController,
    HamlibNetController,
    HamlibRotatorController,
    HamlibVersionChecker,
    RigControlError,
    RigInfo,
    RigState,
    RotatorState,
    VersionInfo,
    _build_mode_map,
    _MockRig,
)

# ---------------------------------------------------------------------------
# モードマップ
# ---------------------------------------------------------------------------


class TestModeMap:
    def test_contains_fm(self) -> None:
        m = _build_mode_map()
        assert "FM" in m

    def test_contains_ssb(self) -> None:
        m = _build_mode_map()
        assert "SSB" in m

    def test_all_values_are_int(self) -> None:
        for v in _build_mode_map().values():
            assert isinstance(v, int)

    def test_known_modes_present(self) -> None:
        m = _build_mode_map()
        for mode in ("FM", "SSB", "LSB", "CW", "CW-R", "DIGITALVOICE", "BPSK", "AFSK", "AM"):
            assert mode in m


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_rig_state_enum(self) -> None:
        assert RigState.DISCONNECTED.value == "disconnected"
        assert RigState.CONNECTED.value == "connected"

    def test_rig_info_fields(self) -> None:
        info = RigInfo(
            model_id=3081,
            model_name="IC-9700",
            port="/dev/ttyUSB0",
            baud_rate=9600,
            state=RigState.CONNECTED,
        )
        assert info.model_id == 3081
        assert info.state == RigState.CONNECTED

    def test_frequency_state_defaults(self) -> None:
        fs = FrequencyState()
        assert fs.freq_hz == 0.0
        assert fs.mode == "FM"
        assert fs.ctcss_tone == 0.0

    def test_rotator_state_defaults(self) -> None:
        rs = RotatorState()
        assert rs.azimuth_deg == 0.0
        assert rs.elevation_deg == 0.0
        assert not rs.is_moving

    def test_version_info_outdated_message(self) -> None:
        vi = VersionInfo(
            installed="4.5.0",
            latest="4.6.0",
            is_outdated=True,
            release_url="https://example.com",
        )
        assert "4.5.0" in vi.warning_message
        assert "4.6.0" in vi.warning_message

    def test_version_info_not_outdated_no_message(self) -> None:
        vi = VersionInfo(installed="4.6.0", latest="4.6.0", is_outdated=False)
        assert vi.warning_message == ""


# ---------------------------------------------------------------------------
# _MockRig
# ---------------------------------------------------------------------------


class TestMockRig:
    def setup_method(self) -> None:
        self.rig = _MockRig(1)

    def test_set_get_freq(self) -> None:
        self.rig.set_freq(0, 145_800_000.0)
        assert self.rig.get_freq(0) == 145_800_000.0

    def test_set_get_mode(self) -> None:
        self.rig.set_mode(2, 3000, 0)
        mode, pb = self.rig.get_mode(0)
        assert mode == 2
        assert pb == 3000

    def test_func_and_level_no_error(self) -> None:
        self.rig.set_func(0, 0, 1)
        self.rig.set_level(0, 0, 885)

    def test_close_no_error(self) -> None:
        self.rig.close()


# ---------------------------------------------------------------------------
# HamlibDirectController — モック環境
# ---------------------------------------------------------------------------


class TestHamlibDirectController:
    def _make_ctrl(self) -> HamlibDirectController:
        return HamlibDirectController(
            model_id=1,
            port="/dev/null",
            baud_rate=9600,
        )

    def test_initial_state_disconnected(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.state == RigState.DISCONNECTED
        assert not ctrl.is_connected

    def test_connect_succeeds_in_mock_mode(self) -> None:
        ctrl = self._make_ctrl()
        # Hamlib なし環境では _MockRig にフォールバックするので connect() は True
        if not HAMLIB_AVAILABLE:
            assert ctrl.connect() is True
            assert ctrl.is_connected

    def test_disconnect_from_disconnected_is_safe(self) -> None:
        ctrl = self._make_ctrl()
        ctrl.disconnect()  # should not raise

    def test_set_frequency_when_disconnected_returns_false(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.set_frequency(145_800_000.0) is False

    def test_get_frequency_when_disconnected_returns_minus1(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.get_frequency() == -1.0

    def test_set_mode_when_disconnected_returns_false(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.set_mode("FM") is False

    def test_get_mode_when_disconnected_returns_fm(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.get_mode() == "FM"

    def test_get_rig_info_when_disconnected_returns_none(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.get_rig_info() is None

    @pytest.mark.skipif(HAMLIB_AVAILABLE, reason="mock-only test")
    def test_full_workflow_in_mock_mode(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.connect()
        assert ctrl.is_connected

        assert ctrl.set_frequency(145_800_000.0)
        assert ctrl.get_frequency() == 145_800_000.0

        assert ctrl.set_mode("FM", 15000)
        assert ctrl.get_mode() == "FM"

        assert ctrl.set_ctcss_tone(88.5)
        assert ctrl.set_ctcss_tone(0.0)
        assert ctrl.set_dcs_code(23)
        assert ctrl.set_dcs_code(0)
        assert ctrl.set_vfo("VFOB")

        info = ctrl.get_rig_info()
        assert info is not None
        assert info.state == RigState.CONNECTED

        ctrl.disconnect()
        assert not ctrl.is_connected

    @pytest.mark.skipif(HAMLIB_AVAILABLE, reason="mock-only test")
    def test_connect_twice_is_idempotent(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.connect()
        assert ctrl.connect()  # 2回目も True
        ctrl.disconnect()

    def test_mode_to_hamlib_unknown_falls_back_to_fm(self) -> None:
        ctrl = self._make_ctrl()
        fm_val = ctrl._mode_to_hamlib("FM")
        assert ctrl._mode_to_hamlib("UNKNOWN_MODE") == fm_val

    def test_hamlib_to_mode_roundtrip(self) -> None:
        ctrl = self._make_ctrl()
        for mode_str in ("FM", "SSB", "CW"):
            code = ctrl._mode_to_hamlib(mode_str)
            assert ctrl._hamlib_to_mode(code) == mode_str


# ---------------------------------------------------------------------------
# HamlibNetController — socket をモック
# ---------------------------------------------------------------------------


class TestHamlibNetController:
    def _make_ctrl(self) -> HamlibNetController:
        return HamlibNetController(host="localhost", port=4532)

    def test_initial_state_disconnected(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.state == RigState.DISCONNECTED

    def test_connect_fails_when_no_server(self) -> None:
        ctrl = self._make_ctrl()
        # ソケット接続をモックして環境依存を排除する
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = ConnectionRefusedError("connection refused")
            mock_cls.return_value = mock_sock
            result = ctrl.connect()
        assert result is False
        assert ctrl.state == RigState.ERROR

    def test_operations_when_disconnected_are_safe(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.set_frequency(145_800_000.0) is False
        assert ctrl.get_frequency() == -1.0
        assert ctrl.set_mode("FM") is False
        assert ctrl.get_mode() == "FM"
        assert ctrl.set_ctcss_tone(88.5) is False
        assert ctrl.set_dcs_code(23) is False
        assert ctrl.set_vfo("VFOA") is False
        assert ctrl.get_rig_info() is None

    def test_disconnect_when_disconnected_is_safe(self) -> None:
        ctrl = self._make_ctrl()
        ctrl.disconnect()

    def _make_connected_ctrl(self) -> HamlibNetController:
        """モックソケットを注入した接続済みコントローラーを返す。"""
        ctrl = self._make_ctrl()
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"RPRT 0\n"
        ctrl._sock = mock_sock
        with ctrl._lock:
            ctrl._state = RigState.CONNECTED
        return ctrl

    def test_set_frequency_sends_command(self) -> None:
        ctrl = self._make_connected_ctrl()
        result = ctrl.set_frequency(145_800_000.0)
        assert result is True
        ctrl._sock.sendall.assert_called()  # type: ignore[union-attr]

    def test_get_frequency_parses_response(self) -> None:
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"145800000\nRPRT 0\n"  # type: ignore[union-attr]
        freq = ctrl.get_frequency()
        assert freq == 145_800_000.0

    def test_get_frequency_returns_minus1_on_bad_response(self) -> None:
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"RPRT -1\n"  # type: ignore[union-attr]
        freq = ctrl.get_frequency()
        assert freq == -1.0

    def test_set_mode_sends_command(self) -> None:
        ctrl = self._make_connected_ctrl()
        result = ctrl.set_mode("FM", 15000)
        assert result is True
        ctrl._sock.sendall.assert_called()  # type: ignore[union-attr]

    def test_get_mode_parses_fm(self) -> None:
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"FM\nRPRT 0\n"  # type: ignore[union-attr]
        assert ctrl.get_mode() == "FM"

    def test_get_mode_parses_usb_as_ssb(self) -> None:
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"USB\nRPRT 0\n"  # type: ignore[union-attr]
        assert ctrl.get_mode() == "SSB"

    def test_get_rig_info_returns_cached_model_name(self) -> None:
        """get_rig_info はキャッシュ済みモデル名を返しソケット I/O を行わない。"""
        ctrl = self._make_connected_ctrl()
        ctrl._cached_model_name = "IC-9700"
        ctrl._sock.reset_mock()  # type: ignore[union-attr]
        info = ctrl.get_rig_info()
        assert info is not None
        assert "localhost" in info.port
        assert info.model_name == "IC-9700"
        ctrl._sock.sendall.assert_not_called()  # type: ignore[union-attr]

    def test_disconnect_closes_socket(self) -> None:
        ctrl = self._make_connected_ctrl()
        sock = ctrl._sock
        ctrl.disconnect()
        sock.close.assert_called()  # type: ignore[union-attr]
        assert ctrl.state == RigState.DISCONNECTED

    # -- VFO 制御 --

    def test_is_connected_false_when_sock_none(self) -> None:
        """ソケットが None のとき is_connected は False（状態が CONNECTED でも）。"""
        ctrl = self._make_ctrl()
        with ctrl._lock:
            ctrl._state = RigState.CONNECTED
        assert ctrl._sock is None
        assert ctrl.is_connected is False

    def test_normalize_vfo_known_names(self) -> None:
        """_normalize_vfo は既知の VFO 文字列をそのまま返す。"""
        assert HamlibNetController._normalize_vfo("VFOA") == "VFOA"
        assert HamlibNetController._normalize_vfo("VFOB") == "VFOB"
        assert HamlibNetController._normalize_vfo("Main") == "Main"
        assert HamlibNetController._normalize_vfo("Sub") == "Sub"

    def test_vfo_mode_false_sends_v_then_f(self) -> None:
        """vfo_mode=False のとき V {vfo}\\nF {freq} の順でコマンドを送信する。"""
        ctrl = self._make_connected_ctrl()
        ctrl._vfo_mode = False
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_frequency(144_800_000.0, "VFOA")
        sent = b"".join(calls)
        assert b"V VFOA\n" in sent
        assert b"F 144800000\n" in sent
        assert sent.index(b"V VFOA\n") < sent.index(b"F 144800000\n")

    def test_vfo_mode_true_sends_set_freq(self) -> None:
        """vfo_mode=True のとき \\\\set_freq {vfo} {freq} を送信する。"""
        ctrl = self._make_connected_ctrl()
        ctrl._vfo_mode = True
        ctrl.set_frequency(144_800_000.0, "VFOA")
        ctrl._sock.sendall.assert_called_with(b"\\set_freq VFOA 144800000\n")  # type: ignore[union-attr]

    def test_set_frequency_raises_rig_control_error_on_failure(self) -> None:
        """接続中に RPRT != 0 が返ったとき RigControlError を送出する。"""
        ctrl = self._make_connected_ctrl()
        ctrl._vfo_mode = True
        ctrl._sock.recv.return_value = b"RPRT -1\n"  # type: ignore[union-attr]
        with pytest.raises(RigControlError):
            ctrl.set_frequency(144_800_000.0, "VFOA")

    def test_detect_vfo_mode_true(self) -> None:
        """rigctld が "1\\nRPRT 0" を返すとき _detect_vfo_mode() は True を返す。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"1\nRPRT 0\n"  # type: ignore[union-attr]
        assert ctrl._detect_vfo_mode() is True

    def test_detect_vfo_mode_false(self) -> None:
        """rigctld が "0\\nRPRT 0" を返すとき _detect_vfo_mode() は False を返す。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"0\nRPRT 0\n"  # type: ignore[union-attr]
        assert ctrl._detect_vfo_mode() is False

    def test_detect_vfo_mode_unsupported(self) -> None:
        """rigctld が RPRT -1（非対応）を返すとき _detect_vfo_mode() は False を返す。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"RPRT -1\n"  # type: ignore[union-attr]
        assert ctrl._detect_vfo_mode() is False

    def test_detect_vfo_mode_timeout_keeps_connection(self) -> None:
        """タイムアウト時に接続を破壊せず False を返す。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.side_effect = TimeoutError("timed out")  # type: ignore[union-attr]
        result = ctrl._detect_vfo_mode()
        assert result is False
        # ソケットは閉じられていない
        assert ctrl._sock is not None
        # 接続状態は CONNECTED のまま
        assert ctrl.state == RigState.CONNECTED

    def test_set_frequency_disconnected_returns_false(self) -> None:
        """未接続のとき set_frequency は False を返す（例外なし）。"""
        ctrl = self._make_ctrl()
        assert ctrl.set_frequency(144_800_000.0, "VFOA") is False

    def test_set_frequency_vfob(self) -> None:
        """VFOB 指定で V VFOB と F コマンドが送信される。"""
        ctrl = self._make_connected_ctrl()
        ctrl._vfo_mode = False
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_frequency(145_900_000.0, "VFOB")
        sent = b"".join(calls)
        assert b"V VFOB\n" in sent
        assert b"F 145900000\n" in sent

    # -- set_vfo_frequencies --

    def test_set_vfo_frequencies_disconnected_returns_false(self) -> None:
        """未接続のとき False を返す（例外なし）。"""
        ctrl = self._make_ctrl()
        assert ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0) is False

    def test_set_vfo_frequencies_first_cycle_sends_F_I_only(self) -> None:
        """初回（_last=None）は F/I のみ送信し f/i は一切送らない。
        readback 廃止 + 先頭ダイアルチェックなし → シーケンス: F → I
        """
        ctrl = self._make_connected_ctrl()
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)
        sent = b"".join(calls)
        assert b"F 145000000\n" in sent
        assert b"I 144000000\n" in sent
        assert b"f\n" not in sent
        assert b"i\n" not in sent
        assert b"\\set_freq" not in sent
        assert b"\\set_split_freq" not in sent
        assert b"\\set_split_vfo" not in sent

    def test_set_vfo_frequencies_dl_only_no_tx(self) -> None:
        """ul_hz=None のとき RX サイクル（F のみ）を送信し TX サイクルを省略する。
        初回（_last=None）は readback も先頭チェックもなし → F のみ送信。
        """
        ctrl = self._make_connected_ctrl()
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_000.0, None)
        sent = b"".join(calls)
        assert b"F 145000000\n" in sent
        assert b"f\n" not in sent
        assert b"I " not in sent
        assert b"i\n" not in sent

    def test_set_vfo_frequencies_raises_on_rprt_error(self) -> None:
        """RPRT != 0 のとき RigControlError を送出する。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.return_value = b"RPRT -1\n"  # type: ignore[union-attr]
        with pytest.raises(RigControlError):
            ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)

    def test_set_vfo_frequencies_first_cycle_no_f_i(self) -> None:
        """初回（_last=None）は f/i を一切送らない（先頭チェックなし・readback なし）。
        S 1 Main 直後の CAT 遅延回避のため。初回シーケンス: F → I のみ。
        """
        ctrl = self._make_connected_ctrl()
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)
        sent = b"".join(calls)
        assert b"F 145000000\n" in sent
        assert b"I 144000000\n" in sent
        assert b"f\n" not in sent
        assert b"i\n" not in sent

    def test_set_vfo_frequencies_sends_nothing_when_freq_unchanged(self) -> None:
        """前回と同じ周波数（変化 < 1 Hz）のとき F も I も f も i も一切送らない。"""
        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.0
        ctrl._last_ul_hz = 144_000_000.0
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        result = ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)
        assert calls == []  # 何も送信しない
        assert result is True

    def test_set_vfo_frequencies_sends_F_when_freq_changes_by_1hz(self) -> None:
        """1 Hz 以上変化したとき F を送る（境界値テスト）。"""
        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.0
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_001.0, None)
        sent = b"".join(calls)
        assert b"F 145000001\n" in sent

    def test_set_vfo_frequencies_skips_F_when_change_less_than_1hz(self) -> None:
        """0.9 Hz の変化では F を送らない（境界値テスト）。"""
        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.9
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_000.0, None)  # diff = 0.9 Hz < 1.0
        sent = b"".join(calls)
        assert b"F " not in sent

    def test_disconnect_resets_last_frequencies(self) -> None:
        """disconnect() で _last_dl_hz と _last_ul_hz が None にリセットされる。"""
        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.0
        ctrl._last_ul_hz = 144_000_000.0
        ctrl.disconnect()
        assert ctrl._last_dl_hz is None
        assert ctrl._last_ul_hz is None

    def test_set_vfo_frequencies_sends_F_when_last_is_none(self) -> None:
        """_last_dl_hz=None（connect直後）は値に関わらず必ず F/I を送る。"""
        ctrl = self._make_connected_ctrl()
        assert ctrl._last_dl_hz is None
        assert ctrl._last_ul_hz is None
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(435_000_000.0, 145_000_000.0)
        sent = b"".join(calls)
        assert b"F 435000000\n" in sent
        assert b"I 145000000\n" in sent

    def test_connect_resets_last_frequencies(self) -> None:
        """connect() 後は _last_dl_hz と _last_ul_hz が必ず None にリセットされる。"""
        ctrl = self._make_ctrl()
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_sock.recv.return_value = b"RPRT 0\n"
            mock_cls.return_value = mock_sock
            ctrl.connect()
        assert ctrl._last_dl_hz is None
        assert ctrl._last_ul_hz is None

    def test_set_vfo_frequencies_second_cycle_sends_F_only_on_change(self) -> None:
        """2 サイクル目以降は f/i を送らず、変化があるときのみ F を送る。"""
        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.0  # 2サイクル目を再現
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_001_000.0, None)
        sent = b"".join(calls)
        assert b"f\n" not in sent  # f/i は一切送らない
        assert b"F 145001000\n" in sent

    def test_set_vfo_frequencies_skips_tx_when_disconnected_between_rx_and_tx(self) -> None:
        """RX サイクル後に切断した場合 TX サイクルをスキップして True を返す。

        シナリオ: 同一周波数（F 送信なし） → RX/TX 間のガードが切断を検出
        """
        from unittest.mock import PropertyMock

        ctrl = self._make_connected_ctrl()
        ctrl._last_dl_hz = 145_000_000.0  # 変化なし → F 送信なし
        ctrl._last_ul_hz = None
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]

        # is_connected: 初回 True（入り口通過）→ ガード False（TX スキップ）
        with patch.object(
            HamlibNetController, "is_connected", new_callable=PropertyMock
        ) as mock_prop:
            mock_prop.side_effect = [True, False]
            result = ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)

        assert result is True
        assert b"F " not in b"".join(calls)
        assert b"I " not in b"".join(calls)

    def test_connect_sends_split_main(self) -> None:
        """connect() 時に S 1 Main（split ON）を送信する。"""
        ctrl = self._make_ctrl()
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_sock.recv.return_value = b"RPRT 0\n"
            mock_cls.return_value = mock_sock
            result = ctrl.connect()
        assert result is True
        sent = b"".join(call.args[0] for call in mock_sock.sendall.call_args_list)
        assert b"S 1 Main\n" in sent

    def test_fetch_model_name_timeout_keeps_connection(self) -> None:
        """_ がタイムアウトしても接続を維持し host:port を返す。"""
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.side_effect = TimeoutError("timed out")  # type: ignore[union-attr]
        name = ctrl._fetch_model_name()
        assert name == "localhost:4532"
        assert ctrl._sock is not None
        assert ctrl.state == RigState.CONNECTED

    def test_init_vfo_timeout_disconnects(self) -> None:
        """S 1 Main がタイムアウトすると _cmd() がソケットを閉じて DISCONNECTED になる。

        raw socket 直接アクセスではなく _cmd() 経由にしたことで、
        タイムアウト後の応答データがバッファに残留してコマンド応答がずれる
        バッファ汚染を起こさなくなった。
        """
        ctrl = self._make_connected_ctrl()
        ctrl._sock.recv.side_effect = TimeoutError("timed out")  # type: ignore[union-attr]
        ctrl._init_vfo()  # should not raise
        assert ctrl._sock is None
        assert ctrl.state == RigState.DISCONNECTED

    def test_connect_returns_false_when_S1Main_fails(self) -> None:
        """S 1 Main がタイムアウトした場合 connect() は False を返し ERROR 状態になる。

        以前は _init_vfo() 失敗を無視して True を返していたため、
        接続ボタンが「接続済み」のまま固まる問題があった。
        """
        ctrl = self._make_ctrl()
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            # TCP 接続自体は成功、S 1 Main の recv でタイムアウト
            mock_sock.connect.return_value = None
            mock_sock.recv.side_effect = TimeoutError("timed out")
            mock_cls.return_value = mock_sock
            result = ctrl.connect()
        assert result is False
        assert ctrl.state == RigState.ERROR
        assert ctrl._sock is None

    # -- queue_mode --

    def test_queue_mode_stores_dl_pending(self) -> None:
        """queue_mode(dl) は _pending_dl_mode に保存する。"""
        ctrl = self._make_connected_ctrl()
        ctrl.queue_mode("USB")
        assert ctrl._pending_dl_mode == "USB"

    def test_queue_mode_unknown_mode_not_stored(self) -> None:
        """未知のモードは _pending_dl_mode に保存されない。"""
        ctrl = self._make_connected_ctrl()
        ctrl.queue_mode("UNKNOWN")
        assert ctrl._pending_dl_mode is None

    def test_disconnect_clears_pending_mode(self) -> None:
        """disconnect() で _pending_dl_mode がクリアされる。"""
        ctrl = self._make_connected_ctrl()
        ctrl._pending_dl_mode = "FM"
        ctrl.disconnect()
        assert ctrl._pending_dl_mode is None

    # -- _init_vfo: split ON → mode --

    def test_init_vfo_sends_pending_mode_after_split(self) -> None:
        """_init_vfo() は S 1 Main の後に pending mode を送信してクリアする。"""
        ctrl = self._make_connected_ctrl()
        ctrl._pending_dl_mode = "USB"
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl._init_vfo()
        sent = b"".join(calls)
        assert b"S 1 Main\n" in sent
        assert b"M USB 0\n" in sent
        assert sent.index(b"S 1 Main\n") < sent.index(b"M USB 0\n")
        assert ctrl._pending_dl_mode is None

    def test_init_vfo_no_mode_if_not_pending(self) -> None:
        """pending mode がなければ M コマンドを送らない。"""
        ctrl = self._make_connected_ctrl()
        assert ctrl._pending_dl_mode is None
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl._init_vfo()
        sent = b"".join(calls)
        assert b"M " not in sent

    # -- set_vfo_frequencies: mode after F/I --

    def test_set_vfo_frequencies_sends_mode_after_freq(self) -> None:
        """pending mode がある場合、M コマンドを F/I より後に送る（V コマンドなし）。"""
        ctrl = self._make_connected_ctrl()
        ctrl.queue_mode("USB")
        calls: list[bytes] = []
        ctrl._sock.sendall.side_effect = lambda data: calls.append(data)  # type: ignore[union-attr]
        ctrl.set_vfo_frequencies(145_000_000.0, 144_000_000.0)
        sent = b"".join(calls)
        assert b"M USB 0\n" in sent
        assert b"V " not in sent
        assert b"F 145000000\n" in sent
        assert b"I 144000000\n" in sent
        idx_f = sent.index(b"F 145000000\n")
        idx_i = sent.index(b"I 144000000\n")
        idx_m = sent.index(b"M USB 0\n")
        assert idx_f < idx_m
        assert idx_i < idx_m

    def test_pending_mode_cleared_after_vfo_frequencies(self) -> None:
        """set_vfo_frequencies() 後は _pending_dl_mode が None になる。"""
        ctrl = self._make_connected_ctrl()
        ctrl.queue_mode("FM")
        ctrl.set_vfo_frequencies(145_000_000.0, None)
        assert ctrl._pending_dl_mode is None

    # -- send_mode_only --

    def test_send_mode_only_sends_v_sub_dl_v_main_ul(self) -> None:
        """send_mode_only は V Sub → M {dl} 0 → V Main → M {ul} 0 の順で送信する。"""
        ctrl = self._make_ctrl()
        sent: list[bytes] = []
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"RPRT 0\n"
        mock_sock.sendall.side_effect = lambda data: sent.append(data)
        with patch("rig.controller.socket.socket", return_value=mock_sock):
            ctrl.send_mode_only("FM", "FM")
        data = b"".join(sent)
        assert b"V Sub\n" in data
        assert b"M FM 0\n" in data
        assert b"V Main\n" in data
        assert data.index(b"V Sub\n") < data.index(b"V Main\n")

    def test_send_mode_only_invert_usb_dl_lsb_ul(self) -> None:
        """invert=True の場合: dl=USB (Sub) / ul=LSB (Main) が正しく送られる。"""
        ctrl = self._make_ctrl()
        sent: list[bytes] = []
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"RPRT 0\n"
        mock_sock.sendall.side_effect = lambda data: sent.append(data)
        with patch("rig.controller.socket.socket", return_value=mock_sock):
            ctrl.send_mode_only("USB", "LSB")  # dl=USB, ul=LSB (RS-44 style)
        data = b"".join(sent)
        # V Sub must precede M USB 0 (downlink)
        assert b"V Sub\n" in data
        assert b"M USB 0\n" in data
        idx_vsub = data.index(b"V Sub\n")
        idx_usb = data.index(b"M USB 0\n")
        assert idx_vsub < idx_usb
        # V Main must precede M LSB 0 (uplink) and come after V Sub
        assert b"V Main\n" in data
        assert b"M LSB 0\n" in data
        idx_vmain = data.index(b"V Main\n")
        idx_lsb = data.index(b"M LSB 0\n")
        assert idx_vmain < idx_lsb
        assert idx_vsub < idx_vmain

    def test_send_mode_only_does_not_send_s1main(self) -> None:
        """send_mode_only は S 1 Main を送信しない（split状態を壊さない）。"""
        ctrl = self._make_ctrl()
        sent: list[bytes] = []
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"RPRT 0\n"
        mock_sock.sendall.side_effect = lambda data: sent.append(data)
        with patch("rig.controller.socket.socket", return_value=mock_sock):
            ctrl.send_mode_only("USB", "USB")
        data = b"".join(sent)
        assert b"S 1 Main\n" not in data

    def test_send_mode_only_unknown_mode_does_nothing(self) -> None:
        """両モードが未知のとき何も送信しない。"""
        ctrl = self._make_ctrl()
        with patch("rig.controller.socket.socket") as mock_cls:
            ctrl.send_mode_only("UNKNOWN", "UNKNOWN")
        mock_cls.assert_not_called()

    def test_send_mode_only_ssb_maps_to_usb(self) -> None:
        """SSB は rigctld の USB として送信される。"""
        ctrl = self._make_ctrl()
        sent: list[bytes] = []
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"RPRT 0\n"
        mock_sock.sendall.side_effect = lambda data: sent.append(data)
        with patch("rig.controller.socket.socket", return_value=mock_sock):
            ctrl.send_mode_only("SSB", "SSB")
        data = b"".join(sent)
        assert b"M USB 0\n" in data

    def test_send_mode_only_silently_ignores_oserror(self) -> None:
        """OSError（接続失敗など）を無視して例外を送出しない。"""
        ctrl = self._make_ctrl()
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_cls.return_value.connect.side_effect = OSError("refused")
            ctrl.send_mode_only("FM", "FM")  # must not raise

    def test_send_mode_only_uses_independent_socket(self) -> None:
        """send_mode_only は main の _sock を使わず独立したソケットを開く。"""
        ctrl = self._make_connected_ctrl()
        original_sock = ctrl._sock
        # Create the new-socket mock before entering the patch block so that
        # socket.socket is still the real class and spec= doesn't fail.
        mock_new_sock = MagicMock(spec=socket.socket)
        mock_new_sock.recv.return_value = b"RPRT 0\n"
        with patch("rig.controller.socket.socket", return_value=mock_new_sock):
            ctrl.send_mode_only("FM", "FM")
        assert ctrl._sock is original_sock  # main socket unchanged


# ---------------------------------------------------------------------------
# HamlibRotatorController
# ---------------------------------------------------------------------------


class TestHamlibRotatorController:
    def _make_ctrl(self) -> HamlibRotatorController:
        return HamlibRotatorController(model_id=1, port="/dev/null")

    def test_initial_state_disconnected(self) -> None:
        ctrl = self._make_ctrl()
        assert not ctrl.is_connected

    def test_connect_mock_mode(self) -> None:
        if not HAMLIB_AVAILABLE:
            ctrl = self._make_ctrl()
            assert ctrl.connect()
            assert ctrl.is_connected
            ctrl.disconnect()

    def test_operations_when_disconnected_are_safe(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.set_position(180.0, 45.0) is False
        state = ctrl.get_position()
        assert state.azimuth_deg == 0.0
        assert ctrl.stop() is False
        assert ctrl.park() is False

    @pytest.mark.skipif(HAMLIB_AVAILABLE, reason="mock-only test")
    def test_full_workflow_mock(self) -> None:
        ctrl = self._make_ctrl()
        assert ctrl.connect()

        assert ctrl.set_position(180.0, 45.0)
        state = ctrl.get_position()
        assert state.azimuth_deg == 180.0
        assert state.elevation_deg == 45.0
        assert state.is_moving

        assert ctrl.stop()
        assert ctrl.park()
        ctrl.disconnect()
        assert not ctrl.is_connected

    def test_net_mode_connect_fails_without_server(self) -> None:
        ctrl = HamlibRotatorController(net_mode=True, net_host="localhost", net_port=4533)
        # ソケット接続をモックして環境依存を排除する
        with patch("rig.controller.socket.socket") as mock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = ConnectionRefusedError("connection refused")
            mock_cls.return_value = mock_sock
            result = ctrl.connect()
        assert result is False

    def _make_net_ctrl_connected(self) -> HamlibRotatorController:
        ctrl = HamlibRotatorController(net_mode=True)
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = b"180.0 45.0\n"
        ctrl._sock = mock_sock
        with ctrl._lock:
            ctrl._state = RigState.CONNECTED
        return ctrl

    def test_net_set_position_sends_command(self) -> None:
        ctrl = self._make_net_ctrl_connected()
        assert ctrl.set_position(270.0, 30.0)
        ctrl._sock.sendall.assert_called()  # type: ignore[union-attr]

    def test_net_get_position_parses_response(self) -> None:
        ctrl = self._make_net_ctrl_connected()
        state = ctrl.get_position()
        assert state.azimuth_deg == 180.0
        assert state.elevation_deg == 45.0

    def test_net_stop_sends_command(self) -> None:
        ctrl = self._make_net_ctrl_connected()
        assert ctrl.stop()
        ctrl._sock.sendall.assert_called_with(b"S\n")  # type: ignore[union-attr]

    def test_net_park_sends_command(self) -> None:
        ctrl = self._make_net_ctrl_connected()
        assert ctrl.park()
        ctrl._sock.sendall.assert_called_with(b"K\n")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# HamlibVersionChecker
# ---------------------------------------------------------------------------


class TestHamlibVersionChecker:
    def test_get_installed_version_returns_string(self) -> None:
        checker = HamlibVersionChecker()
        ver = checker.get_installed_version()
        assert isinstance(ver, str)
        assert len(ver) > 0

    def test_not_installed_returns_not_installed(self) -> None:
        if not HAMLIB_AVAILABLE:
            checker = HamlibVersionChecker()
            assert checker.get_installed_version() == "not installed"

    def test_version_lt_basic(self) -> None:
        assert HamlibVersionChecker._version_lt("4.5.0", "4.6.0")
        assert HamlibVersionChecker._version_lt("4.5.0", "4.5.1")
        assert HamlibVersionChecker._version_lt("3.9.9", "4.0.0")
        assert not HamlibVersionChecker._version_lt("4.6.0", "4.5.0")
        assert not HamlibVersionChecker._version_lt("4.6.0", "4.6.0")

    def test_version_lt_handles_non_numeric(self) -> None:
        # クラッシュしないことを確認
        assert isinstance(HamlibVersionChecker._version_lt("4.5.x", "4.6.0"), bool)

    @pytest.mark.asyncio
    async def test_check_version_network_error_returns_safe_result(self) -> None:
        """ネットワーク不通時は is_outdated=False で返す。"""
        checker = HamlibVersionChecker()
        with patch("rig.controller.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("unreachable")
            mock_client_cls.return_value = mock_client

            result = await checker.check_version()

        assert isinstance(result, VersionInfo)
        assert result.is_outdated is False
        assert isinstance(result.installed, str)

    @pytest.mark.asyncio
    async def test_check_version_detects_outdated(self) -> None:
        """インストール版より新しいリリースがある場合 is_outdated=True。"""
        checker = HamlibVersionChecker()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/Hamlib/Hamlib/releases/tag/v99.0.0",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("rig.controller.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            # Hamlib がない環境では "not installed" → is_outdated=False になるので
            # インストール済みバージョンをモックする
            with patch.object(checker, "get_installed_version", return_value="4.5.0"):
                result = await checker.check_version()

        assert result.latest == "99.0.0"
        assert result.is_outdated is True
        assert "99.0.0" in result.warning_message

    @pytest.mark.asyncio
    async def test_check_version_not_outdated_when_current(self) -> None:
        """インストール版が最新と同じなら is_outdated=False。"""
        checker = HamlibVersionChecker()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tag_name": "v4.5.0",
            "html_url": "https://example.com",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("rig.controller.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch.object(checker, "get_installed_version", return_value="4.5.0"):
                result = await checker.check_version()

        assert result.is_outdated is False
        assert result.warning_message == ""
