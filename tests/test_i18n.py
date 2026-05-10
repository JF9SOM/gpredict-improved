"""
i18n モジュールのユニットテスト

ネットワーク接続不要。.mo ファイルが存在する環境で実行する。
"""

from __future__ import annotations

import pytest

import i18n as i18n_mod


@pytest.fixture(autouse=True)
def reset_language() -> None:
    """各テスト前後に英語（デフォルト）にリセットする。"""
    i18n_mod.set_language("en")
    yield
    i18n_mod.set_language("en")


class TestSetLanguage:
    def test_default_is_english(self) -> None:
        assert i18n_mod.get_language() == "en"

    def test_set_japanese(self) -> None:
        i18n_mod.set_language("ja")
        assert i18n_mod.get_language() == "ja"

    def test_set_back_to_english(self) -> None:
        i18n_mod.set_language("ja")
        i18n_mod.set_language("en")
        assert i18n_mod.get_language() == "en"

    def test_set_unknown_language_falls_back(self) -> None:
        """存在しない言語コードはフォールバックしてもクラッシュしない。"""
        i18n_mod.set_language("xx")
        assert i18n_mod.get_language() == "xx"
        # フォールバック後は恒等変換
        assert i18n_mod._("Ready") == "Ready"


class TestTranslation:
    def test_english_passthrough(self) -> None:
        i18n_mod.set_language("en")
        assert i18n_mod._("Ready") == "Ready"
        assert i18n_mod._("Settings") == "Settings"

    def test_japanese_translation(self) -> None:
        i18n_mod.set_language("ja")
        assert i18n_mod._("Ready") == "準備完了"
        assert i18n_mod._("Settings") == "設定"
        assert i18n_mod._("Satellite Tracker") == "衛星追尾ソフト"

    def test_japanese_menu_translations(self) -> None:
        i18n_mod.set_language("ja")
        assert i18n_mod._("File") == "ファイル"
        assert i18n_mod._("View") == "表示"
        assert i18n_mod._("Radio") == "無線機"
        assert i18n_mod._("Tools") == "ツール"
        assert i18n_mod._("Help") == "ヘルプ"

    def test_japanese_status_messages(self) -> None:
        i18n_mod.set_language("ja")
        assert i18n_mod._("Updating TLE data...") == "TLEデータを更新中..."
        assert i18n_mod._("No radio connected") == "無線機未接続"

    def test_unknown_msgid_returns_msgid(self) -> None:
        """未登録の文字列はそのまま返す（フォールバック動作）。"""
        i18n_mod.set_language("ja")
        assert i18n_mod._("__nonexistent_key__") == "__nonexistent_key__"

    def test_language_switch_at_runtime(self) -> None:
        """実行中の言語切り替えが正しく反映される。"""
        i18n_mod.set_language("en")
        assert i18n_mod._("Quit") == "Quit"
        i18n_mod.set_language("ja")
        assert i18n_mod._("Quit") == "終了"
        i18n_mod.set_language("en")
        assert i18n_mod._("Quit") == "Quit"


class TestNgettext:
    def test_english_singular(self) -> None:
        i18n_mod.set_language("en")
        result = i18n_mod.ngettext("%(count)d satellite", "%(count)d satellites", 1)
        assert result == "%(count)d satellite"

    def test_english_plural(self) -> None:
        i18n_mod.set_language("en")
        result = i18n_mod.ngettext("%(count)d satellite", "%(count)d satellites", 3)
        assert result == "%(count)d satellites"

    def test_japanese_always_singular_form(self) -> None:
        """日本語は単複同形（nplurals=1）。"""
        i18n_mod.set_language("ja")
        singular = i18n_mod.ngettext("%(count)d satellite", "%(count)d satellites", 1)
        plural = i18n_mod.ngettext("%(count)d satellite", "%(count)d satellites", 5)
        assert singular == plural == "%(count)d 衛星"


class TestAvailableLanguages:
    def test_english_always_available(self) -> None:
        langs = i18n_mod.available_languages()
        assert "en" in langs

    def test_japanese_available(self) -> None:
        langs = i18n_mod.available_languages()
        assert "ja" in langs

    def test_returns_list(self) -> None:
        assert isinstance(i18n_mod.available_languages(), list)

    def test_english_is_first(self) -> None:
        """英語は常にリストの先頭。"""
        langs = i18n_mod.available_languages()
        assert langs[0] == "en"
