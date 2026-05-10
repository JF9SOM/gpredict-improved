# GPredict-Improved

🌐 日本語 | [English](README.md)

**GPredict の現代的後継** — アマチュア衛星追尾ソフトウェア

[![CI](https://github.com/JF9SOM/gpredict-improved/actions/workflows/ci.yml/badge.svg)](https://github.com/JF9SOM/gpredict-improved/actions)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)

GPredict-Improved は、長年アマチュア無線家に愛用されてきた
[GPredict](https://github.com/csete/gpredict)（Alexandru Csete OZ9AEC 作）の設計を引き継ぎつつ、
現代的な Python スタックで一から作り直したソフトウェアです。

---

## 主な改善点

| 機能 | GPredict | GPredict-Improved |
|------|----------|-------------------|
| プラットフォーム | デスクトップのみ | デスクトップ + **同一LAN内のスマホ・タブレットからブラウザでアクセス** |
| 無線機制御 | rigctld を別途起動 | **Hamlib 内蔵** — GUIで無線機を選択するだけ |
| ドップラー補正 | 周波数のみ | **周波数 + モード + CTCSSトーン**を自動設定 |
| 衛星周波数DB | SATNOGSのみ・テキスト編集 | SATNOGS自動同期 + **GUIから手動追加・編集** |
| TLE更新 | 手動 | **複数ソースから自動更新・品質スコアリング** |
| 対応OS | Linux (GTK+) | **Linux・Windows・macOS・Raspberry Pi** |

---

## インストール（Ubuntu/Debian）

```bash
# 1. システム依存パッケージ
sudo apt install python3.11 python3-pip libhamlib-dev python3-hamlib

# 2. リポジトリをクローン
git clone https://github.com/JF9SOM/gpredict-improved.git
cd gpredict-improved

# 3. Python仮想環境
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# 4. udevルール設定（USB無線機アクセス権）
sudo cp scripts/99-gpredict-improved.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo usermod -aG dialout $USER
# 一度ログアウト・ログインしてください

# 5. 起動
gpredict-improved
```

### Windows / macOS

最初のタグバージョン公開後、
[Releases](https://github.com/JF9SOM/gpredict-improved/releases) ページに
ビルド済みインストーラーを掲載予定です。

---

## アーキテクチャ

```
gpredict-improved/
├── src/
│   ├── core/     # 衛星追尾エンジン（Skyfield）— 仰角・ドップラー・パス予測
│   ├── ui/       # PySide6 Qt6 デスクトップUI
│   ├── web/      # FastAPI + WebSocket（LAN内ブラウザアクセス、ポート8080）
│   ├── rig/      # Hamlib 無線機・ローテーター制御
│   ├── data/     # TLE/SATNOGS同期・SQLite DB・手動入力
│   └── i18n/     # 多言語対応（gettextベース）
├── locale/
│   ├── en/LC_MESSAGES/   # 英語文字列
│   └── ja/LC_MESSAGES/   # 日本語文字列
└── tests/
```

起動時の動作：
1. Qt6メインウィンドウを起動
2. FastAPI/uvicorn をバックグラウンドスレッドで起動（ポート8080）
3. `DataSyncManager` がTLE・SATNOGSデータを自動取得（初回または期限切れ時）
4. ステータスバーにLAN内アクセスURL + QRコードボタンを表示

---

## 開発環境セットアップ

```bash
pip install -e ".[dev]"
pytest              # テスト実行
ruff check .        # リント
mypy src/           # 型チェック

# .po ファイルを編集した後は再コンパイル
msgfmt locale/en/LC_MESSAGES/gpredict_improved.po \
      -o locale/en/LC_MESSAGES/gpredict_improved.mo
msgfmt locale/ja/LC_MESSAGES/gpredict_improved.po \
      -o locale/ja/LC_MESSAGES/gpredict_improved.mo
```

Claude Code 向け開発指示書は [CLAUDE.md](CLAUDE.md) を参照してください。

---

## 新しい言語を追加する

1. `locale/en/LC_MESSAGES/gpredict_improved.po` を
   `locale/<言語コード>/LC_MESSAGES/gpredict_improved.po` にコピー
2. `msgstr` の行を翻訳
3. コンパイル: `msgfmt locale/<言語コード>/LC_MESSAGES/gpredict_improved.po -o locale/<言語コード>/LC_MESSAGES/gpredict_improved.mo`
4. 新しい言語は設定ダイアログに自動で表示されます

---

## ライセンス

GPL-2.0-or-later（GPredict互換）

---

## 謝辞

- [GPredict](https://github.com/csete/gpredict) — Alexandru Csete OZ9AEC
- [Skyfield](https://rhodesmill.org/skyfield/) — Brandon Rhodes
- [Hamlib](https://hamlib.github.io/) — Hamlib Development Team
- [SATNOGS](https://satnogs.org/) — Libre Space Foundation
