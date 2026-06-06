"""
Internationalization (i18n) foundation module.

Translation system based on Python standard gettext.
To add a new language, add locale/<lang>/LC_MESSAGES/gpredict_improved.po
and compile it with msgfmt.

Usage:
    from i18n import _, ngettext, set_language

    set_language("ja")
    print(_("Satellite Tracker"))   # -> translated string
"""

from __future__ import annotations

import gettext
import sys
import threading
from pathlib import Path

_DOMAIN = "gpredict_improved"

# Resolve locale/ directory.  When running from a PyInstaller bundle the
# _MEIPASS root is the extraction directory, so locale/ lives directly under it.
# In a normal source checkout it is three levels above this file (project root).
_LOCALE_DIR = (
    Path(sys._MEIPASS) / "locale"  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent.parent / "locale"
)

_lock = threading.Lock()
_current_lang: str = "en"
_translation: gettext.NullTranslations = gettext.NullTranslations()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _(message: str) -> str:
    """Translate a message to the current language.

    Even if other modules do ``from i18n import _`` before set_language() is called,
    they always reference the latest translation catalog.
    """
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate with plural form support."""
    return _translation.ngettext(singular, plural, n)


def set_language(lang: str) -> None:
    """Change the active language.

    Args:
        lang: Language code (e.g. "en", "ja").
              Falls back to English (identity transform) if no matching .mo file exists.
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
            # Fall back to English when no .mo file is found
            translation = gettext.NullTranslations()

        _translation = translation
        _current_lang = lang


def get_language() -> str:
    """Return the current language code (e.g. "en", "ja")."""
    return _current_lang


def available_languages() -> list[str]:
    """Return a list of available language codes. Always includes "en"."""
    langs: list[str] = ["en"]
    if _LOCALE_DIR.exists():
        for lang_dir in sorted(_LOCALE_DIR.iterdir()):
            if not lang_dir.is_dir() or lang_dir.name == "en":
                continue
            mo = lang_dir / "LC_MESSAGES" / f"{_DOMAIN}.mo"
            if mo.exists():
                langs.append(lang_dir.name)
    return langs
