"""
多言語対応（i18n）基盤モジュール

Python 標準 gettext ベースの翻訳システム。
新言語は locale/<lang>/LC_MESSAGES/gpredict_improved.po を追加して
msgfmt でコンパイルするだけで対応可能。

使い方:
    from i18n import _, ngettext, set_language

    set_language("ja")
    print(_("Satellite Tracker"))   # -> "衛星追尾ソフト"
"""

from __future__ import annotations

import gettext
import threading
from pathlib import Path

_DOMAIN = "gpredict_improved"
# プロジェクトルート直下の locale/ ディレクトリ
_LOCALE_DIR = Path(__file__).parent.parent.parent / "locale"

_lock = threading.Lock()
_current_lang: str = "en"
_translation: gettext.NullTranslations = gettext.NullTranslations()


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def _(message: str) -> str:
    """メッセージを現在の言語に翻訳して返す。

    他モジュールが ``from i18n import _`` した後に set_language() を呼んでも、
    常に最新の翻訳カタログを参照する。
    """
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """複数形に対応した翻訳関数。"""
    return _translation.ngettext(singular, plural, n)


def set_language(lang: str) -> None:
    """アクティブな言語を変更する。

    Args:
        lang: 言語コード（"en"、"ja" など）。
              対応する .mo ファイルが存在しない場合は英語（恒等変換）にフォールバック。
    """
    global _translation, _current_lang

    with _lock:
        if lang == "en":
            _translation = gettext.NullTranslations()
            _current_lang = "en"
            return

        translation: gettext.NullTranslations
        try:
            translation = gettext.translation(
                _DOMAIN,
                localedir=str(_LOCALE_DIR),
                languages=[lang],
            )
        except FileNotFoundError:
            # .mo ファイルが存在しない場合は英語にフォールバック
            translation = gettext.NullTranslations()

        _translation = translation
        _current_lang = lang


def get_language() -> str:
    """現在の言語コードを返す（例: "en"、"ja"）。"""
    return _current_lang


def available_languages() -> list[str]:
    """利用可能な言語コードの一覧を返す。常に "en" を含む。"""
    langs: list[str] = ["en"]
    if _LOCALE_DIR.exists():
        for lang_dir in sorted(_LOCALE_DIR.iterdir()):
            if not lang_dir.is_dir() or lang_dir.name == "en":
                continue
            mo = lang_dir / "LC_MESSAGES" / f"{_DOMAIN}.mo"
            if mo.exists():
                langs.append(lang_dir.name)
    return langs
