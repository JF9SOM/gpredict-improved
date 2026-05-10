# GPredict-Improved

**GPredict の現代的後継** — アマチュア衛星追尾ソフトウェア

[![CI](https://github.com/JF9SOM/gpredict-improved/actions/workflows/ci.yml/badge.svg)](https://github.com/JF9SOM/gpredict-improved/actions)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)

GPredict-Improved は、長年アマチュア無線家に愛用されてきた
[GPredict](https://github.com/csete/gpredict) の設計を引き継ぎつつ、
現代的な技術スタックで一から作り直したソフトウェアです。

---

## 主な改善点

| 機能 | GPredict | GPredict-Improved |
|------|----------|-------------------|
| プラットフォーム | デスクトップのみ | デスクトップ + **同一LAN内のスマホ・タブレットからブラウザでアクセス** |
| 無線機制御 | rigctld を別途起動必要 | **Hamlib 内蔵** — GUIで無線機を選択するだけ |
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
# → 一度ログアウト・ログインしてください

# 5. 起動
gpredict-improved
```

---

## 開発環境セットアップ

```bash
pip install -e ".[dev]"
pytest          # テスト実行
ruff check .    # リント
```

詳細は [CLAUDE.md](CLAUDE.md) を参照してください（Claude Code向け開発指示書も兼ねています）。

---

## ライセンス

GPL-2.0-or-later（GPredict互換）

---

## 謝辞

- [GPredict](https://github.com/csete/gpredict) — Alexandru Csete OZ9AEC
- [Skyfield](https://rhodesmill.org/skyfield/) — Brandon Rhodes
- [Hamlib](https://hamlib.github.io/) — Hamlib Development Team
- [SATNOGS](https://satnogs.org/) — Libre Space Foundation
