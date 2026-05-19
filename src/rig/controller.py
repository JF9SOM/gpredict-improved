"""
Hamlib 無線機・ローテーター制御モジュール

RigController       — 無線機制御の抽象基底クラス
HamlibDirectController — python-hamlib で直接 COMポート接続
HamlibNetController — TCP 経由で rigctld に接続（GPredict NET Control 互換）
RotatorController   — ローテーター制御の抽象基底クラス
HamlibRotatorController — Hamlib ローテーター制御
HamlibVersionChecker   — インストール済み Hamlib バージョンチェック

Hamlib 未インストール環境では自動的にモックにフォールバックするため、
CI（python-hamlib なし）でもテストが通る。
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hamlib import — 未インストール時はモックにフォールバック
# ---------------------------------------------------------------------------

try:
    import Hamlib as _hamlib_mod

    HAMLIB_AVAILABLE = True
except ModuleNotFoundError:
    _hamlib_mod = None
    HAMLIB_AVAILABLE = False
    logger.warning(
        "python-hamlib not found — running in mock mode. "
        "Install libhamlib-dev and python3-hamlib to enable real rig control."
    )


# ---------------------------------------------------------------------------
# モードマッピング（SATNOGS モード文字列 → Hamlib 定数）
# ---------------------------------------------------------------------------


def _build_mode_map() -> dict[str, int]:
    """Hamlib が使えるときは実定数を、モック時はダミー整数をマップする。"""
    if HAMLIB_AVAILABLE:
        return {
            "DIGITALVOICE": _hamlib_mod.RIG_MODE_FM,
            "FM": _hamlib_mod.RIG_MODE_FM,
            "SSB": _hamlib_mod.RIG_MODE_USB,
            "LSB": _hamlib_mod.RIG_MODE_LSB,
            "CW": _hamlib_mod.RIG_MODE_CW,
            "CW-R": _hamlib_mod.RIG_MODE_CWR,
            "BPSK": _hamlib_mod.RIG_MODE_PKTUSB,
            "AFSK": _hamlib_mod.RIG_MODE_PKTFM,
            "AM": _hamlib_mod.RIG_MODE_AM,
        }
    return {
        "DIGITALVOICE": 1,
        "FM": 1,
        "SSB": 2,
        "LSB": 3,
        "CW": 4,
        "CW-R": 5,
        "BPSK": 6,
        "AFSK": 7,
        "AM": 8,
    }


MODE_MAP: dict[str, int] = _build_mode_map()


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


class RigState(Enum):
    """無線機接続状態。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class RigInfo:
    """接続中の無線機情報。"""

    model_id: int
    model_name: str
    port: str
    baud_rate: int
    state: RigState = RigState.DISCONNECTED


@dataclass
class FrequencyState:
    """現在の周波数・モード状態。"""

    freq_hz: float = 0.0
    mode: str = "FM"
    passband_hz: int = 0
    ctcss_tone: float = 0.0  # Hz (0.0 = off)
    dcs_code: int = 0  # 0 = off


@dataclass
class RotatorState:
    """ローテーター状態。"""

    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0
    is_moving: bool = False


@dataclass
class VersionInfo:
    """Hamlib バージョン情報と更新確認結果。"""

    installed: str
    latest: str
    is_outdated: bool
    release_url: str = ""
    warning_message: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.is_outdated:
            self.warning_message = (
                f"Hamlib {self.installed} is installed, "
                f"but {self.latest} is available. "
                f"Consider upgrading: {self.release_url}"
            )


class RigControlError(Exception):
    """無線機制御エラー（rigctld コマンド失敗・通信エラー時に送出）。"""


# ---------------------------------------------------------------------------
# 抽象基底クラス — RigController
# ---------------------------------------------------------------------------


class RigController(ABC):
    """
    無線機制御の抽象基底クラス。

    すべての公開メソッドはスレッドセーフ（内部ロックで保護）。
    Qt UI スレッドと追尾バックグラウンドスレッドの両方から呼ばれる。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RigState.DISCONNECTED
        self._freq_state = FrequencyState()

    # -- 接続管理 --

    @abstractmethod
    def connect(self) -> bool:
        """接続を確立する。成功時 True。"""

    @abstractmethod
    def disconnect(self) -> None:
        """接続を切断する。"""

    @property
    def state(self) -> RigState:
        """現在の接続状態。"""
        with self._lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        """接続中かどうか。"""
        return self.state == RigState.CONNECTED

    # -- 周波数・モード --

    @abstractmethod
    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """周波数を Hz 単位で設定する。"""

    @abstractmethod
    def get_frequency(self, vfo: str = "VFOA") -> float:
        """現在の周波数を Hz で返す。エラー時は -1.0。"""

    @abstractmethod
    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        """モードを設定する。mode は SATNOGS 形式文字列（"FM", "SSB" など）。"""

    @abstractmethod
    def get_mode(self, vfo: str = "VFOA") -> str:
        """現在のモードを SATNOGS 形式文字列で返す。"""

    # -- CTCSS / DCS トーン --

    @abstractmethod
    def set_ctcss_tone(self, tone_hz: float) -> bool:
        """CTCSS トーンを設定する（0.0 で無効化）。"""

    @abstractmethod
    def set_dcs_code(self, code: int) -> bool:
        """DCS コードを設定する（0 で無効化）。"""

    # -- VFO --

    @abstractmethod
    def set_vfo(self, vfo: str) -> bool:
        """アクティブ VFO を切り替える（"VFOA" / "VFOB" / "Main" / "Sub"）。"""

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """VFOA・VFOB の周波数を安全に設定する。

        サブクラスでオーバーライド可能。デフォルトは set_frequency を順次呼ぶ。
        未接続時は False。失敗時は RigControlError を送出する。
        """
        ok = True
        if vfoa_hz is not None:
            ok = self.set_frequency(vfoa_hz, "VFOA") and ok
        if vfob_hz is not None:
            ok = self.set_frequency(vfob_hz, "VFOB") and ok
        return ok

    # -- ユーティリティ --

    @abstractmethod
    def get_rig_info(self) -> RigInfo | None:
        """接続中の無線機情報を返す。未接続時は None。"""

    def _mode_to_hamlib(self, mode: str) -> int:
        """SATNOGS モード文字列を Hamlib 定数に変換する。未知のモードは FM 扱い。"""
        return MODE_MAP.get(mode, MODE_MAP["FM"])

    def _hamlib_to_mode(self, hamlib_mode: int) -> str:
        """Hamlib 定数を SATNOGS モード文字列に変換する。"""
        reverse = {v: k for k, v in MODE_MAP.items()}
        return reverse.get(hamlib_mode, "FM")


# ---------------------------------------------------------------------------
# HamlibDirectController
# ---------------------------------------------------------------------------


class HamlibDirectController(RigController):
    """
    python-hamlib を使ってシリアルポートに直接接続する無線機コントローラー。

    Hamlib 未インストール時はモックとして動作する。
    """

    def __init__(
        self,
        model_id: int,
        port: str,
        baud_rate: int = 9600,
        data_bits: int = 8,
        stop_bits: int = 1,
        handshake: str = "None",
    ) -> None:
        """
        Args:
            model_id:  Hamlib rig model ID（例: IC-9700 = 3081）
            port:      シリアルポート（"/dev/ttyUSB0", "COM3" など）
            baud_rate: ボーレート
            data_bits: データビット数
            stop_bits: ストップビット数
            handshake: フロー制御 ("None", "XONXOFF", "Hardware")
        """
        super().__init__()
        self._model_id = model_id
        self._port = port
        self._baud_rate = baud_rate
        self._data_bits = data_bits
        self._stop_bits = stop_bits
        self._handshake = handshake
        self._rig: Any = None  # Hamlib.Rig instance or mock

    # -- 接続管理 --

    def connect(self) -> bool:
        """シリアルポートに接続する。"""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            if HAMLIB_AVAILABLE:
                rig = _hamlib_mod.Rig(self._model_id)
                rig.state.rigport.pathname = self._port
                rig.state.rigport.parm.serial.rate = self._baud_rate
                rig.state.rigport.parm.serial.data_bits = self._data_bits
                rig.state.rigport.parm.serial.stop_bits = self._stop_bits
                rig.open()
                self._rig = rig
            else:
                self._rig = _MockRig(self._model_id)

            with self._lock:
                self._state = RigState.CONNECTED
            logger.info("RigDirect: connected to %s (model %d)", self._port, self._model_id)
            return True

        except Exception as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("RigDirect: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """シリアルポートを切断する。"""
        with self._lock:
            if self._state == RigState.DISCONNECTED:
                return
        try:
            if HAMLIB_AVAILABLE and self._rig is not None:
                self._rig.close()
        except Exception as exc:
            logger.warning("RigDirect: disconnect error — %s", exc)
        finally:
            self._rig = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    # -- 周波数・モード --

    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """周波数を設定する。"""
        if not self.is_connected or self._rig is None:
            return False
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            self._rig.set_freq(hamlib_vfo, freq_hz)
            with self._lock:
                self._freq_state.freq_hz = freq_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_frequency: %s", exc)
            return False

    def get_frequency(self, vfo: str = "VFOA") -> float:
        """現在の周波数を返す。"""
        if not self.is_connected or self._rig is None:
            return -1.0
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            return float(self._rig.get_freq(hamlib_vfo))
        except Exception as exc:
            logger.error("RigDirect.get_frequency: %s", exc)
            return -1.0

    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        """モードとパスバンドを設定する。"""
        if not self.is_connected or self._rig is None:
            return False
        try:
            hamlib_mode = self._mode_to_hamlib(mode)
            hamlib_vfo = self._vfo_str_to_const(vfo)
            self._rig.set_mode(hamlib_mode, passband_hz, hamlib_vfo)
            with self._lock:
                self._freq_state.mode = mode
                self._freq_state.passband_hz = passband_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_mode: %s", exc)
            return False

    def get_mode(self, vfo: str = "VFOA") -> str:
        """現在のモードを返す。"""
        if not self.is_connected or self._rig is None:
            return "FM"
        try:
            hamlib_vfo = self._vfo_str_to_const(vfo)
            mode, _ = self._rig.get_mode(hamlib_vfo)
            return self._hamlib_to_mode(mode)
        except Exception as exc:
            logger.error("RigDirect.get_mode: %s", exc)
            return "FM"

    def set_ctcss_tone(self, tone_hz: float) -> bool:
        """CTCSS トーンを設定する。tone_hz=0.0 で無効化。"""
        if not self.is_connected or self._rig is None:
            return False
        if not HAMLIB_AVAILABLE:
            with self._lock:
                self._freq_state.ctcss_tone = tone_hz
            return True
        try:
            # Hamlib は tone を 10倍整数（例: 88.5 Hz → 885）で扱う
            tone_int = int(round(tone_hz * 10))
            if tone_hz > 0:
                self._rig.set_func(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_FUNC_TONE,
                    1,
                )
                self._rig.set_level(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_LEVEL_CTCSS_TONE,
                    tone_int,
                )
            else:
                self._rig.set_func(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_FUNC_TONE,
                    0,
                )
            with self._lock:
                self._freq_state.ctcss_tone = tone_hz
            return True
        except Exception as exc:
            logger.error("RigDirect.set_ctcss_tone: %s", exc)
            return False

    def set_dcs_code(self, code: int) -> bool:
        """DCS コードを設定する。code=0 で無効化。"""
        if not self.is_connected or self._rig is None:
            return False
        if not HAMLIB_AVAILABLE:
            with self._lock:
                self._freq_state.dcs_code = code
            return True
        try:
            if code > 0:
                self._rig.set_func(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_FUNC_TSQL,
                    1,
                )
                self._rig.set_level(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_LEVEL_CTCSS_SQL,
                    code,
                )
            else:
                self._rig.set_func(
                    _hamlib_mod.RIG_VFO_CURR,
                    _hamlib_mod.RIG_FUNC_TSQL,
                    0,
                )
            with self._lock:
                self._freq_state.dcs_code = code
            return True
        except Exception as exc:
            logger.error("RigDirect.set_dcs_code: %s", exc)
            return False

    def set_vfo(self, vfo: str) -> bool:
        """アクティブ VFO を切り替える。"""
        if not self.is_connected or self._rig is None:
            return False
        try:
            self._rig.set_vfo(self._vfo_str_to_const(vfo))
            return True
        except Exception as exc:
            logger.error("RigDirect.set_vfo: %s", exc)
            return False

    def get_rig_info(self) -> RigInfo | None:
        """接続中の無線機情報を返す。"""
        if not self.is_connected:
            return None
        model_name = f"Model {self._model_id}"
        if HAMLIB_AVAILABLE and self._rig is not None:
            with contextlib.suppress(Exception):
                model_name = self._rig.caps.model_name
        return RigInfo(
            model_id=self._model_id,
            model_name=model_name,
            port=self._port,
            baud_rate=self._baud_rate,
            state=self.state,
        )

    # -- 内部ユーティリティ --

    def _vfo_str_to_const(self, vfo: str) -> int:
        """VFO 文字列を Hamlib 定数または整数に変換する。"""
        if not HAMLIB_AVAILABLE:
            return 0
        vfo_map = {
            "VFOA": _hamlib_mod.RIG_VFO_A,
            "VFOB": _hamlib_mod.RIG_VFO_B,
            "Main": _hamlib_mod.RIG_VFO_MAIN,
            "Sub": _hamlib_mod.RIG_VFO_SUB,
        }
        return int(vfo_map.get(vfo, _hamlib_mod.RIG_VFO_CURR))


# ---------------------------------------------------------------------------
# HamlibNetController（rigctld TCP 接続）
# ---------------------------------------------------------------------------


class HamlibNetController(RigController):
    """
    TCP 経由で rigctld に接続する無線機コントローラー。

    GPredict NET Control モードと互換性があり、既存の rigctld セットアップを
    そのまま利用できる。独自プロトコル（改行区切りテキスト）を使用。
    """

    _TIMEOUT = 10.0  # seconds — FTX-1 等の低速 CAT バックエンドに対応

    def __init__(self, host: str = "localhost", port: int = 4532) -> None:
        """
        Args:
            host: rigctld が動作しているホスト
            port: rigctld のポート番号（デフォルト 4532）
        """
        super().__init__()
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._vfo_mode: bool = False
        self._cmd_lock = threading.Lock()  # send+recv を直列化してレスポンスのズレを防ぐ
        self._cached_model_name: str = ""  # 接続時に一度だけ取得してキャッシュする

    # -- 接続管理 --

    @property
    def is_connected(self) -> bool:
        """接続中かつソケットが有効なときのみ True。"""
        with self._lock:
            return self._state == RigState.CONNECTED and self._sock is not None

    def connect(self) -> bool:
        """rigctld への TCP 接続を確立する。"""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._TIMEOUT)
            sock.connect((self._host, self._port))
            self._sock = sock
            with self._lock:
                self._state = RigState.CONNECTED
            logger.info("RigNet: connected to %s:%d", self._host, self._port)
            # _ と \chk_vfo はオプショナルな情報取得コマンド。
            # raw socket で 2s タイムアウト付きで送ると、FTX-1 等の低速バックエンドでは
            # タイムアウト後に応答がバッファに残留し、後続の _cmd() 呼び出しが
            # 別コマンドの応答を誤読する（コマンド/応答ずれ）。
            # 接続シーケンスでは送らず、S 1 Main のみ送る。
            self._init_vfo()
            return True
        except OSError as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("RigNet: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """TCP 接続を切断する。"""
        with self._lock:
            if self._state == RigState.DISCONNECTED:
                return
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        finally:
            self._sock = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    # -- 低レベル通信 --

    def _cmd(self, command: str) -> str:
        """rigctld にコマンドを送り、応答を返す。

        すべてのコマンドで RPRT 行が現れるまで読み続ける。
        これにより読み取りコマンド（f/i 等）の応答行がバッファに残って
        次コマンドの応答と混在する問題を防ぐ。
        _cmd_lock で send+recv を直列化して複数スレッドの応答ズレを防ぐ。
        OSError 発生時はソケットを閉じて DISCONNECTED に遷移する。
        """
        with self._cmd_lock:
            if self._sock is None:
                return ""
            try:
                self._sock.sendall((command + "\n").encode())
                data = b""
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"RPRT" in data:
                        break
                return data.decode(errors="replace").strip()
            except OSError as exc:
                logger.error("RigNet._cmd(%r): %s", command, exc)
                with contextlib.suppress(OSError):
                    if self._sock:
                        self._sock.close()
                self._sock = None
                with self._lock:
                    self._state = RigState.DISCONNECTED
                return ""

    def _fetch_model_name(self) -> str:
        """接続時に一度だけ _ コマンドでモデル名を取得する。

        _cmd() を経由せず直接ソケットを操作する。_ コマンドに非対応の rigctld や
        タイムアウトが発生しても接続を破壊せず "host:port" にフォールバックする。
        """
        if self._sock is None:
            return f"{self._host}:{self._port}"
        prev_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(2.0)
            self._sock.sendall(b"_\n")
            data = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"RPRT" in data:
                    break
            resp = data.decode(errors="replace").strip()
            lines = [ln.strip() for ln in resp.splitlines() if ln.strip() and not ln.startswith("RPRT")]
            return lines[0] if lines else f"{self._host}:{self._port}"
        except OSError as exc:
            logger.warning("RigNet: _ (get_info) failed (ignored): %s", exc)
            return f"{self._host}:{self._port}"
        finally:
            with contextlib.suppress(OSError):
                if self._sock is not None:
                    self._sock.settimeout(prev_timeout)

    def _init_vfo(self) -> None:
        """split ON + TX VFO = Main を設定する（接続時1回だけ）。

        tcpdump で確認した本家 gpredict のシーケンス: S 1 Main
        _cmd() 経由で送信するため _cmd_lock で直列化され、
        raw socket の独立した recv ループによるバッファ残留が起きない。
        """
        resp = self._cmd("S 1 Main")
        if "RPRT 0" not in resp:
            logger.warning("RigNet: split setup returned %r", resp)

    # -- 内部ユーティリティ --

    def _detect_vfo_mode(self) -> bool:
        """\\chk_vfo を送信して rigctld の VFO モードを検出する。

        _cmd() を経由せず直接 socket を操作することで、タイムアウトや
        コマンド非対応時でも接続を破壊せずに False を返す。

        応答形式（rigctld）:
          vfo_mode=on  → "1\\nRPRT 0\\n"
          vfo_mode=off → "0\\nRPRT 0\\n"
          非対応        → "RPRT -1\\n"
          タイムアウト  → OSError (socket.timeout)
        """
        if self._sock is None:
            return False
        prev_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(2.0)
            self._sock.sendall(b"\\chk_vfo\n")
            data = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"RPRT" in data:
                    break
            resp = data.decode(errors="replace").strip()
            lines = resp.splitlines()
            return bool(lines and lines[0].strip() == "1")
        except OSError as exc:
            logger.warning("RigNet: \\chk_vfo failed (vfo_mode=False assumed): %s", exc)
            return False
        finally:
            with contextlib.suppress(OSError):
                if self._sock is not None:
                    self._sock.settimeout(prev_timeout)

    @staticmethod
    def _normalize_vfo(vfo: str) -> str:
        """VFO 文字列を rigctld が受け付ける形式に正規化する。"""
        _map = {"VFOA": "VFOA", "VFOB": "VFOB", "Main": "Main", "Sub": "Sub"}
        return _map.get(vfo, vfo)

    # -- 周波数・モード --

    def _set_one_vfo(self, vfo: str, freq_hz: float) -> None:
        """単一 VFO の周波数を設定する内部ヘルパー。失敗時は RigControlError。"""
        norm_vfo = self._normalize_vfo(vfo)
        if self._vfo_mode:
            resp = self._cmd(f"\\set_freq {norm_vfo} {int(freq_hz)}")
        else:
            vfo_resp = self._cmd(f"V {norm_vfo}")
            if "RPRT 0" not in vfo_resp:
                raise RigControlError(f"set_vfo({norm_vfo!r}) failed: {vfo_resp!r}")
            resp = self._cmd(f"F {int(freq_hz)}")
        if "RPRT 0" not in resp:
            raise RigControlError(f"set_frequency({freq_hz!r}, {norm_vfo!r}) failed: {resp!r}")
        with self._lock:
            self._freq_state.freq_hz = freq_hz

    def set_frequency(self, freq_hz: float, vfo: str = "VFOA") -> bool:
        """周波数を設定する。

        未接続時は False を返す。
        接続中にコマンドが失敗した場合は RigControlError を送出する。
        split コマンドは送信しない（FTX-1 等の split 問題を回避するため）。
        """
        if not self.is_connected:
            return False
        self._set_one_vfo(vfo, freq_hz)
        return True

    def set_vfo_frequencies(
        self,
        vfoa_hz: float | None,
        vfob_hz: float | None,
    ) -> bool:
        """毎秒の追尾ループで RX/TX 周波数を設定する。

        connect() 時に _init_vfo() が S 1 Main（split ON、TX VFO=Main）を
        1回だけ送信済み。以降このメソッドは F と I のみ送る：
          F {dl_hz}  → Sub（RX/ダウンリンク）に書き込み  → RPRT 0 確認
          I {ul_hz}  → Main（TX/アップリンク）に書き込み → RPRT 0 確認
        ul_hz が None の場合は F のみ実行する。
        """
        if not self.is_connected:
            return False
        if vfoa_hz is not None:
            logger.info("RigNet: sending F %d", int(vfoa_hz))
            resp = self._cmd(f"F {int(vfoa_hz)}")
            if "RPRT 0" not in resp:
                raise RigControlError(f"set RX freq failed: {resp!r}")
            with self._lock:
                self._freq_state.freq_hz = vfoa_hz
        if vfob_hz is not None:
            logger.info("RigNet: sending I %d", int(vfob_hz))
            resp = self._cmd(f"I {int(vfob_hz)}")
            if "RPRT 0" not in resp:
                raise RigControlError(f"set TX freq failed: {resp!r}")
        return True

    def get_frequency(self, vfo: str = "VFOA") -> float:
        resp = self._cmd("f")
        try:
            return float(resp.splitlines()[0])
        except (ValueError, IndexError):
            return -1.0

    def set_mode(self, mode: str, passband_hz: int = 0, vfo: str = "VFOA") -> bool:
        # rigctld の M コマンドは "M <mode> <passband>" 形式
        hamlib_mode_name = _SATNOGS_TO_RIGCTLD_MODE.get(mode, "FM")
        resp = self._cmd(f"M {hamlib_mode_name} {passband_hz}")
        ok = "RPRT 0" in resp
        if ok:
            with self._lock:
                self._freq_state.mode = mode
        return ok

    def get_mode(self, vfo: str = "VFOA") -> str:
        resp = self._cmd("m")
        lines = resp.splitlines()
        if lines:
            rigctld_mode = lines[0].strip()
            return _RIGCTLD_MODE_TO_SATNOGS.get(rigctld_mode, "FM")
        return "FM"

    def set_ctcss_tone(self, tone_hz: float) -> bool:
        tone_int = int(round(tone_hz * 10))
        resp = self._cmd(f"L CTCSS_TONE {tone_int}")
        return "RPRT 0" in resp

    def set_dcs_code(self, code: int) -> bool:
        resp = self._cmd(f"L DCS_CODE {code}")
        return "RPRT 0" in resp

    def set_vfo(self, vfo: str) -> bool:
        resp = self._cmd(f"V {vfo}")
        return "RPRT 0" in resp

    def get_rig_info(self) -> RigInfo | None:
        if not self.is_connected:
            return None
        return RigInfo(
            model_id=0,
            model_name=self._cached_model_name or f"{self._host}:{self._port}",
            port=f"{self._host}:{self._port}",
            baud_rate=0,
            state=self.state,
        )


# rigctld モード名マッピング
_SATNOGS_TO_RIGCTLD_MODE: dict[str, str] = {
    "DIGITALVOICE": "FM",
    "FM": "FM",
    "SSB": "USB",
    "LSB": "LSB",
    "CW": "CW",
    "CW-R": "CWR",
    "BPSK": "PKTUSB",
    "AFSK": "PKTFM",
    "AM": "AM",
}
_RIGCTLD_MODE_TO_SATNOGS: dict[str, str] = {v: k for k, v in _SATNOGS_TO_RIGCTLD_MODE.items()}


# ---------------------------------------------------------------------------
# 抽象基底クラス — RotatorController
# ---------------------------------------------------------------------------


class RotatorController(ABC):
    """ローテーター制御の抽象基底クラス。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RigState.DISCONNECTED
        self._rotor_state = RotatorState()

    @abstractmethod
    def connect(self) -> bool:
        """接続を確立する。"""

    @abstractmethod
    def disconnect(self) -> None:
        """接続を切断する。"""

    @property
    def is_connected(self) -> bool:
        """接続中かどうか。"""
        with self._lock:
            return self._state == RigState.CONNECTED

    @abstractmethod
    def set_position(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """方位角・仰角を設定する（度単位）。"""

    @abstractmethod
    def get_position(self) -> RotatorState:
        """現在の方位角・仰角を返す。"""

    @abstractmethod
    def stop(self) -> bool:
        """回転を停止する。"""

    @abstractmethod
    def park(self) -> bool:
        """ホームポジションに戻す。"""


# ---------------------------------------------------------------------------
# HamlibRotatorController
# ---------------------------------------------------------------------------


class HamlibRotatorController(RotatorController):
    """
    Hamlib を使ったローテーター制御クラス。

    直接接続（HamlibDirect 相当）と NET 接続（rotctld）の両方に対応する。
    net_mode=True のとき rotctld に TCP 接続する。
    """

    def __init__(
        self,
        model_id: int = 1,
        port: str = "/dev/ttyUSB0",
        baud_rate: int = 9600,
        *,
        net_mode: bool = False,
        net_host: str = "localhost",
        net_port: int = 4533,
    ) -> None:
        super().__init__()
        self._model_id = model_id
        self._port = port
        self._baud_rate = baud_rate
        self._net_mode = net_mode
        self._net_host = net_host
        self._net_port = net_port
        self._rot: Any = None
        self._sock: socket.socket | None = None

    def connect(self) -> bool:
        """ローテーターに接続する。"""
        with self._lock:
            if self._state == RigState.CONNECTED:
                return True
            self._state = RigState.CONNECTING

        try:
            if self._net_mode:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self._net_host, self._net_port))
                self._sock = sock
            elif HAMLIB_AVAILABLE:
                rot = _hamlib_mod.Rot(self._model_id)
                rot.state.rotport.pathname = self._port
                rot.state.rotport.parm.serial.rate = self._baud_rate
                rot.open()
                self._rot = rot
            else:
                self._rot = _MockRotator()

            with self._lock:
                self._state = RigState.CONNECTED
            logger.info("Rotator: connected")
            return True
        except Exception as exc:
            with self._lock:
                self._state = RigState.ERROR
            logger.error("Rotator: connect failed — %s", exc)
            return False

    def disconnect(self) -> None:
        """ローテーターを切断する。"""
        try:
            if self._net_mode and self._sock:
                self._sock.close()
            elif self._rot is not None and HAMLIB_AVAILABLE:
                self._rot.close()
        except Exception:
            pass
        finally:
            self._rot = None
            self._sock = None
            with self._lock:
                self._state = RigState.DISCONNECTED

    def set_position(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """方位角・仰角を指定して回転させる。"""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                cmd = f"P {azimuth_deg:.1f} {elevation_deg:.1f}\n"
                self._sock.sendall(cmd.encode())
            elif self._rot is not None:
                self._rot.set_position(azimuth_deg, elevation_deg)

            with self._lock:
                self._rotor_state.azimuth_deg = azimuth_deg
                self._rotor_state.elevation_deg = elevation_deg
                self._rotor_state.is_moving = True
            return True
        except Exception as exc:
            logger.error("Rotator.set_position: %s", exc)
            return False

    def get_position(self) -> RotatorState:
        """現在の方位角・仰角を返す。"""
        if not self.is_connected:
            return RotatorState()
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"p\n")
                data = self._sock.recv(256).decode(errors="replace").strip()
                parts = data.split()
                if len(parts) >= 2:
                    az = float(parts[0])
                    el = float(parts[1])
                    with self._lock:
                        self._rotor_state.azimuth_deg = az
                        self._rotor_state.elevation_deg = el
            elif self._rot is not None:
                az, el = self._rot.get_position()
                with self._lock:
                    self._rotor_state.azimuth_deg = float(az)
                    self._rotor_state.elevation_deg = float(el)
        except Exception as exc:
            logger.error("Rotator.get_position: %s", exc)

        with self._lock:
            return RotatorState(
                azimuth_deg=self._rotor_state.azimuth_deg,
                elevation_deg=self._rotor_state.elevation_deg,
                is_moving=self._rotor_state.is_moving,
            )

    def stop(self) -> bool:
        """回転を停止する。"""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"S\n")
            elif self._rot is not None:
                self._rot.stop()
            with self._lock:
                self._rotor_state.is_moving = False
            return True
        except Exception as exc:
            logger.error("Rotator.stop: %s", exc)
            return False

    def park(self) -> bool:
        """ホームポジションに戻す（rotctld: K コマンド）。"""
        if not self.is_connected:
            return False
        try:
            if self._net_mode and self._sock:
                self._sock.sendall(b"K\n")
            elif self._rot is not None:
                self._rot.park()
            return True
        except Exception as exc:
            logger.error("Rotator.park: %s", exc)
            return False


# ---------------------------------------------------------------------------
# HamlibVersionChecker
# ---------------------------------------------------------------------------


class HamlibVersionChecker:
    """
    インストール済み Hamlib のバージョンを取得し、
    GitHub API で最新リリースと比較して古い場合は警告情報を返す。
    """

    _GITHUB_API = "https://api.github.com/repos/Hamlib/Hamlib/releases/latest"

    def get_installed_version(self) -> str:
        """インストール済み Hamlib バージョン文字列を返す。未インストール時は "not installed"。"""
        if HAMLIB_AVAILABLE:
            try:
                return str(_hamlib_mod.cvar.hamlib_version)
            except Exception:
                return "unknown"
        return "not installed"

    async def check_version(self, timeout: float = 10.0) -> VersionInfo:
        """
        GitHub API で最新バージョンを確認して VersionInfo を返す。

        ネットワーク不通時はインストール済みバージョンのみ返し、
        is_outdated=False（警告なし）とする。
        """
        installed = self.get_installed_version()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._GITHUB_API,
                    headers={"Accept": "application/vnd.github+json"},
                )
                resp.raise_for_status()
                data = resp.json()
                latest = str(data.get("tag_name", "")).lstrip("v")
                release_url = str(data.get("html_url", ""))
        except Exception as exc:
            logger.warning("HamlibVersionChecker: could not fetch latest version — %s", exc)
            return VersionInfo(installed=installed, latest=installed, is_outdated=False)

        is_outdated = installed not in ("not installed", "unknown") and self._version_lt(
            installed, latest
        )
        return VersionInfo(
            installed=installed,
            latest=latest,
            is_outdated=is_outdated,
            release_url=release_url,
        )

    @staticmethod
    def _version_lt(a: str, b: str) -> bool:
        """バージョン文字列 a < b を比較する（セマンティックバージョニング想定）。"""

        def _parts(v: str) -> tuple[int, ...]:
            parts = []
            for seg in v.split(".")[:3]:
                try:
                    parts.append(int(seg))
                except ValueError:
                    parts.append(0)
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)

        return _parts(a) < _parts(b)


# ---------------------------------------------------------------------------
# 内部モッククラス（Hamlib 未インストール環境用）
# ---------------------------------------------------------------------------


class _MockRig:
    """python-hamlib が使えない環境でのスタブ。テストと CI 用。"""

    def __init__(self, model_id: int) -> None:
        self._model_id = model_id
        self._freq: float = 145_800_000.0
        self._mode: int = 1  # FM
        self._passband: int = 15000

    class caps:  # noqa: N801
        model_name = "Mock Rig"

    def set_freq(self, vfo: int, freq: float) -> None:
        self._freq = freq

    def get_freq(self, vfo: int) -> float:
        return self._freq

    def set_mode(self, mode: int, passband: int, vfo: int) -> None:
        self._mode = mode
        self._passband = passband

    def get_mode(self, vfo: int) -> tuple[int, int]:
        return self._mode, self._passband

    def set_func(self, vfo: int, func: int, status: int) -> None:
        pass

    def set_level(self, vfo: int, level: int, value: int) -> None:
        pass

    def set_vfo(self, vfo: int) -> None:
        pass

    def close(self) -> None:
        pass


class _MockRotator:
    """python-hamlib が使えない環境でのローテータースタブ。"""

    def __init__(self) -> None:
        self._az: float = 0.0
        self._el: float = 0.0

    def set_position(self, az: float, el: float) -> None:
        self._az = az
        self._el = el

    def get_position(self) -> tuple[float, float]:
        return self._az, self._el

    def stop(self) -> None:
        pass

    def park(self) -> None:
        pass

    def close(self) -> None:
        pass
