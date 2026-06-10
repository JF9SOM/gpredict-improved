# GPredict-Improved

🌐 日本語 | [English](README.md)

**GPredict の現代的後継** — アマチュア衛星追尾ソフトウェア

[![CI](https://github.com/JF9SOM/gpredict-improved/actions/workflows/ci.yml/badge.svg)](https://github.com/JF9SOM/gpredict-improved/actions)
[![Release](https://img.shields.io/github/v/release/JF9SOM/gpredict-improved)](https://github.com/JF9SOM/gpredict-improved/releases/latest)
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
| 無線機制御 | rigctld を別途起動 | **Hamlib 内蔵**（700機種以上）— GUIで無線機を選択するだけ |
| SDR対応 | なし | **HackRF / RTL-SDR（SoapySDR経由）** — スペクトラム・復調・IQ録音 |
| ドップラー補正 | 周波数のみ | **周波数 + モード + CTCSS/DCSトーン**を自動設定 |
| デュアルリグ | 対応 | **Rig 1 + Rig 2** — SDRドングルをリグとして割り当て可能 |
| 衛星周波数DB | SATNOGSのみ・テキスト編集 | SATNOGS自動同期 + **GUIから手動追加・編集** |
| TLE更新 | 自動更新あり | **複数ソースから自動更新・品質スコアリング** |
| パス予測表示 | リスト表示のみ | **グラフィカルパスチャート + スカイレーダー + 世界地図フットプリント** |
| ローテーター制御 | 別途 rotctld | **Hamlib内蔵 — GUIでローテーターを選択するだけ。rotctld不要** |
| 対応OS | Linux・Windows・macOS（GTK+）| **Linux・Windows・macOS・Raspberry Pi** |

---

## 主な機能

### デスクトップUI（Qt6）
- **ダッシュボード** — ズームマップ + レーダー + ライブステータスバーを一画面に統合
- **世界地図** — 衛星フットプリント・地上軌跡・ドットクリックで衛星選択
- **レーダー（スカイビュー）** — 北固定、AOS/LOS時刻表示、複数衛星を色分け表示
- **パスチャート** — 仰角カーブをグラフ表示（品質ランク色分け：excellent/good/fair/low）
- **グループパスチャート** — 複数衛星のパスをまとめて表示、ホバーでツールチップ
- **アップカミングパス** — 対象衛星またはグループで検索、カレンダー選択、CSV出力
- **ラジオコントロール** — ドップラー補正、モード/CTCSS自動設定、トランスポンダーリスト
- **SDRコントロール** — リアルタイムスペクトラムアナライザー、NFM/USB/LSB/CW復調、IQ録音、トランスポンダーロック付きパスバンドチューニング
- **オートトラック** — 衛星を順次自動追尾（リスト設定可）
- **AOS/LOSデスクトップ通知**（Linux: notify-send / macOS: osascript / Windows: PowerShell）

### モバイルブラウザUI
同じLAN内のスマホ・タブレットからアプリインストール不要でアクセス可能です。

- **Trackingタブ** — 衛星リスト、リアルタイムEL/AZ/距離 + レーダー
- **Antennaタブ** — 大きな数字でAZ/ELを表示、パス進行バー、トランスポンダーカード、リモートRIG接続/切断
- **Pass Predictionタブ** — 衛星ごとのアップカミングパス一覧
- **Group Passタブ** — グループ検索とパス表示
- **コンパス連動レーダー**（Android：デバイスの向きに連動して自動回転）

### 無線機・ローテーター制御
- Hamlib 4.7.1 内蔵 — rigctld 別途起動不要
- NET Controlモード（rigctld/rotctld互換）— 既存環境と併用可能
- デュアルリグ：Rig 1 + Rig 2 独立制御（例：IC-9700 + HackRF）
- 反転トランスポンダー対応、パスバンドチューニング
- キャッチアップ追尾（タイムアウト再送信付き）

### データ管理
- **SATNOGS** トランスポンダーDB 自動同期（日次）
- **コミュニティ周波数DB** — FT4コーリング周波数など、SANTOGSにない慣習周波数
- **TLE複数ソース**: CelesTrak Amateur/CubeSat/Weather/Earth-Obs/Science/Stations、SATNOGS TLE API、手動入力
- TLE品質スコアリング：excellent（6時間未満）/ good（24時間未満）/ fair（72時間未満）/ poor
- 仮NORAD ID（90000番台）を実IDへ自動移行
- 手動入力のTLE・トランスポンダーは自動同期で絶対に上書きされない
- カスタムFavoriteグループ（グループ名・数を自由に設定）

### アプリ内アップデーター
- **Help → Check for Updates** — 最新リリースを自動ダウンロードしてインストール
- **Help → Hamlib Update** — アプリを再インストールせずにHamlibのみアップグレード

---

## インストール

### Windows

[Releases](https://github.com/JF9SOM/gpredict-improved/releases/latest) ページから
`GPredict-Improved-Setup.exe` をダウンロードして実行してください。

### macOS

[Releases](https://github.com/JF9SOM/gpredict-improved/releases/latest) ページから
`GPredict-Improved.dmg` をダウンロードし、開いてアプリケーションフォルダにドラッグしてください。

### Linux（AppImage）

```bash
# ダウンロード後、実行権限を付与して起動
chmod +x GPredict-Improved-*.AppImage
./GPredict-Improved-*.AppImage
```

### Linux（ソースから — Ubuntu/Debian）

```bash
# 1. システム依存パッケージ
sudo apt install python3.11 python3-pip libhamlib-dev python3-hamlib \
                 python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf

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
python -m src.main
```

---

## SDR クイックスタート

1. SDRデバイス（HackRF One、RTL-SDR等）を接続
2. **Settings → Rig Settings → SDR Settings** を開く
3. **Enumerate** をクリックしてデバイスを検出
4. デバイスを選択し、サンプルレート・ゲインを設定して Rig 1 または Rig 2 に割り当て
5. **Connect** をクリック — **SDR Controlタブ** がアクティブになる
6. 衛星とトランスポンダーを選択 — モードが自動設定される

> **SoapySDR** はシステムパッケージとして別途インストールが必要です。
> Linux: `sudo apt install python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf`

---

## アーキテクチャ

```
gpredict-improved/
├── src/
│   ├── core/     # 衛星追尾エンジン（Skyfield）— 仰角・ドップラー・パス予測
│   ├── ui/       # PySide6 Qt6 デスクトップUI
│   ├── web/      # FastAPI + WebSocket（LAN内ブラウザアクセス、ポート8080）
│   ├── rig/      # Hamlib 無線機・ローテーター制御 + SdrRigAdapter
│   ├── sdr/      # SoapySDR バックエンド — デバイス・パイプライン・復調・録音
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

# フォーマット
ruff format src/ tests/

# リント
ruff check src/ tests/

# 型チェック
mypy --strict src/

# テスト（低スペック環境では test_rig.py のみ推奨）
python -m pytest tests/test_rig.py -q

# .po ファイルを編集した後は再コンパイル
msgfmt locale/ja/LC_MESSAGES/gpredict_improved.po \
      -o locale/ja/LC_MESSAGES/gpredict_improved.mo
```

Claude Code 向け開発指示書は [CLAUDE.md](CLAUDE.md) を参照してください。

---

## 動作確認済みハードウェア

| デバイス | 種別 | 備考 |
|---------|------|------|
| Yaesu FTX-1F | トランシーバー | Hamlib 4.7.1 モデル1051、NET Control、ドップラー補正確認済 |
| Yaesu FT-991AM | トランシーバー | Hamlib 4.7.1 モデル1036、NET Control、ドップラー補正確認済 |
| HackRF One | SDR | SoapyHackRF、NFM/USB/CW復調・スペクトラム・Bias-T確認済 |
| RTL-SDR | SDR | SoapyRTLSDR、基本動作確認済 |
| FTX-1F + RTL-SDR | デュアルリグ | パスバンドチューニング + Lock連動確認済 |

---

## 今後の予定（フェーズ2）

### デジタルモード（SDR）
- **HRPT / LRPT** — SatDump連携による気象衛星画像受信
- **APRS** — Direwolf（TCP KISS）経由での受信・デコード
- **FT4 / FT8** — WSJT-X（UDP）との連携
- **衛星テレメトリー** — gr-satellites対応（100機種以上）
- **CW解析** — AIベースのデコーダー（機械学習推論）
- **SSTV** — pySSTV による受信

### UI / UX
- **日本語UI** — 翻訳ファイルはすでに準備済み。フェーズ2で正式対応予定
- **観測ログ** — 運用した衛星パスの記録・集計・エクスポート
- **SDRデバイスインストールダイアログ** — USB VID/PIDスキャン、RTL-SDR/HackRFのドライバーインストール誘導

### ハードウェア
- IC-9700・TS-2000・FT-817ND 等での実機ドップラー制御テスト
- WSJT-X / JS8Call との周波数・モード同期

ご意見・ご要望は下記メーリングリストまでお気軽にどうぞ。

---

## コミュニティ・サポート

質問・機能要望・バグ報告・運用報告など、お気軽にメーリングリストへどうぞ：

**📧 [gpredict-improved@googlegroups.com](mailto:gpredict-improved@googlegroups.com)**

> *GPredict-Improved（アマチュア衛星追尾ソフト）のサポート・議論グループです。*

投稿歓迎なトピック例：
- セットアップ・操作に関する質問
- 機能要望・アイデア
- バグ報告（[GitHub Issues](https://github.com/JF9SOM/gpredict-improved/issues) でも受け付けています）
- 実際に使ってみた衛星・無線機の運用報告

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
- [SoapySDR](https://github.com/pothosware/SoapySDR) — Pothosware
