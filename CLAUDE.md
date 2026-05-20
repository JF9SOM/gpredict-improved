# CLAUDE.md — GPredict-Improved 開発指示書

このファイルはClaude Codeが本プロジェクトを理解し、一貫した判断をするための指示書です。
コードを書く前に必ずこのファイルを参照してください。

---

## プロジェクト概要

**名称**: GPredict-Improved  
**目的**: アマチュア衛星追尾ソフト GPredict の現代的後継ソフトウェア  
**開発言語**: Python 3.11+  
**対象OS**: Linux（主開発環境: Ubuntu）, Windows, macOS  
**ライセンス**: GPL-2.0（GPredict互換）

### GPredict-Improvedが解決する課題
- 現行GPredictはデスクトップ専用 → **同一LAN内のスマホ・タブレットからもブラウザでアクセス可能**にする
- rigctld/rotctldを別途手動起動が必要 → **Hamlibを内蔵してGUIから無線機・ローテーターを直接設定**
- 衛星周波数・モードの設定が隠しテキストファイル編集 → **GUIで追加・編集・削除が可能**
- TLEが手動更新 → **自動更新・品質スコアリング**
- SATNOGSデータのみに依存 → **手動追加・上書き機能付き**

---

## アーキテクチャ

```
gpredict-improved/
├── src/
│   ├── core/           # 衛星追尾エンジン（Skyfield）・ビジネスロジック
│   ├── ui/             # PySide6 Qt6 デスクトップUI
│   ├── web/            # FastAPI + WebSocket（LAN内ブラウザアクセス）
│   ├── rig/            # Hamlib制御（直接接続 + NET Control互換）
│   ├── data/           # データ同期（SATNOGS・TLE）・SQLiteDB・手動編集
│   └── i18n/           # 多言語対応基盤（gettextラッパー）
├── locale/
│   ├── en/LC_MESSAGES/ # 英語翻訳（デフォルト）
│   └── ja/LC_MESSAGES/ # 日本語翻訳
├── tests/
├── docs/
├── scripts/            # udevルール・インストールヘルパー
└── .github/workflows/  # CI/CD（Windows・Mac・Linux自動ビルド）
```

### 起動時の動作
1. Qt6メインウィンドウを起動
2. バックグラウンドスレッドでFastAPI/uvicornをポート8080で起動
3. DataSyncManagerがTLE・SATNOGSデータを自動取得（初回 or 期限切れ時）
4. ステータスバーにLAN内アクセスURL + QRコードボタンを表示

### データフロー
```
SATNOGS API ──┐
Space-Track   ├──→ DataSyncManager ──→ SQLite DB ──→ CoreEngine(Skyfield)
CelesTrak     ┘                                           │
                                                          ├──→ Qt6 UI
手動入力 ──────────────────────────────→ SQLite DB        ├──→ Hamlib RigController
                                                          └──→ FastAPI WebSocket
```

---

## 技術スタック

| 用途 | ライブラリ | バージョン |
|------|-----------|-----------|
| デスクトップUI | PySide6 | >=6.6 |
| 軌道計算 | skyfield | >=1.48 |
| WebサーバーAPI | fastapi | >=0.110 |
| ASGIサーバー | uvicorn | >=0.27 |
| HTTPクライアント | httpx | >=0.27 |
| データベース | sqlite3 | 標準ライブラリ |
| DBマイグレーション | alembic | >=1.13 |
| データモデル | pydantic | >=2.6 |
| Hamlib制御 | Hamlib (python binding) | システム提供 |
| QRコード生成 | qrcode | >=7.4 |
| mDNS | zeroconf | >=0.131 |
| テスト | pytest | >=8.0 |
| パッケージング | PyInstaller | >=6.4 |

---

## コーディング規約

### 全般
- **型ヒント必須**: すべての関数・メソッドに型ヒントを付ける
- **docstring必須**: すべての公開クラス・関数にdocstringを書く（日本語可）
- **フォーマッター**: `ruff format`（black互換）
- **リンター**: `ruff check`
- **型チェック**: `mypy --strict`

### 命名規則
- クラス: `PascalCase`
- 関数・変数: `snake_case`
- 定数: `UPPER_SNAKE_CASE`
- プライベート: `_leading_underscore`

### エラーハンドリング
- ネットワークエラーは必ずキャッチしてローカルキャッシュにフォールバック
- ユーザー向けエラーはQt6のステータスバーかダイアログで表示（コンソールに捨てない）
- Hamlibエラーは接続状態をUIに反映してリトライ可能にする

### 非同期処理
- FastAPIのエンドポイントは `async def`
- Qt6のUIスレッドをブロックしない（重い処理はQThread or asyncio）
- TLE/SATNOGS取得はすべて非同期（httpx AsyncClient）

---

## データベーススキーマ（SQLite）

### satellites テーブル
```sql
CREATE TABLE satellites (
    norad_cat_id    INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    alt_names       TEXT,           -- JSON配列
    status          TEXT,           -- 'alive', 'dead', 'unknown'
    updated_at      DATETIME
);
```

### transmitters テーブル（SATNOGS + 手動）
```sql
CREATE TABLE transmitters (
    uuid            TEXT PRIMARY KEY,   -- SATNOGSのUUID or 'manual-{uuid4}'
    norad_cat_id    INTEGER REFERENCES satellites(norad_cat_id),
    description     TEXT NOT NULL,
    type            TEXT,           -- 'Transmitter', 'Transponder', 'Beacon'
    uplink_low      INTEGER,        -- Hz
    uplink_high     INTEGER,        -- Hz (バンドの場合)
    downlink_low    INTEGER,        -- Hz
    downlink_high   INTEGER,        -- Hz
    mode            TEXT,           -- 'FM', 'SSB', 'CW', 'DIGITALVOICE', etc.
    invert          BOOLEAN DEFAULT FALSE,
    baud            INTEGER,
    ctcss_tone      REAL,           -- Hz (FM用トーン)
    ctcss_tone_type TEXT,           -- 'CTCSS', 'DCS'
    alive           BOOLEAN DEFAULT TRUE,
    source          TEXT DEFAULT 'satnogs',  -- 'satnogs' or 'manual'
    manual_override BOOLEAN DEFAULT FALSE,   -- 手動データがSATNOGSより優先
    notes           TEXT,           -- ユーザーメモ
    updated_at      DATETIME
);
```

### tle_data テーブル
```sql
CREATE TABLE tle_data (
    norad_cat_id    INTEGER PRIMARY KEY REFERENCES satellites(norad_cat_id),
    name            TEXT,
    line1           TEXT NOT NULL,
    line2           TEXT NOT NULL,
    epoch           DATETIME,
    source          TEXT,   -- 'celestrak', 'space-track', 'amsat', 'manual'
    fetched_at      DATETIME,
    quality_score   TEXT    -- 'excellent'(<6h), 'good'(<24h), 'fair'(<72h), 'poor'
);
```

### tle_history テーブル
```sql
CREATE TABLE tle_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    norad_cat_id    INTEGER,
    line1           TEXT,
    line2           TEXT,
    epoch           DATETIME,
    source          TEXT,
    fetched_at      DATETIME
);
```

---

## 主要コンポーネントの設計原則

### CoreEngine (src/core/)
- `SatelliteEngine`: Skyfieldラッパー。パス予測・仰角/方位角/ドップラー計算
- `PassPredictor`: 指定期間のパス一覧を返す
- `DopplerCalculator`: 反転トランスポンダ対応の周波数補正計算
- Qt UIとFastAPI WebSocket両方から使えるようスレッドセーフに設計

### RigController (src/rig/)
- 抽象基底クラス `RigController` を定義
- `HamlibDirectController`: python-hamlibで直接COMポート接続
- `HamlibNetController`: TCP経由でrigctld/rotctldに接続（従来互換）
- `RotatorController`: ローテーター制御（同様の抽象化）
- モード設定・CTCSS/DCSトーン設定・VFO切り替えをサポート

### DataSyncManager (src/data/)
- バックグラウンドで動作（QThread）
- SATNOGS APIから全トランスポンダを日次取得・DBに保存
- TLEを複数ソースから取得（CelesTrak優先、Space-Trackはオプション）
- `manual_override=True` のレコードはSATNOGS上書きから保護
- オフライン時はキャッシュで継続動作

### TransmitterManager (src/data/)
- SATNOGS取得データと手動追加データを統合管理
- 手動追加データはSATNOGSより優先（`manual_override`フラグ）
- GUI経由でCRUD操作が可能
- エクスポート/インポート（JSON）対応

### Web API (src/web/)
- `GET /api/satellites` — 衛星一覧
- `GET /api/satellites/{norad}/transmitters` — トランスポンダ一覧
- `GET /api/satellites/{norad}/passes` — パス予測（以下フィールドを含む）
  - `max_elevation_deg`: 最大仰角（度）
  - `max_elevation_time`: 最大仰角に達する時刻（ISO 8601 UTC）
  - `duration_seconds`: パス継続時間（秒）
  - `quality`: 品質ランク（excellent/good/fair/low）
- `WebSocket /ws/tracking` — リアルタイム仰角/方位角/ドップラー
- `GET /api/tle/status` — TLE品質情報
- `GET /api/location` — 現在の自局位置情報を返す
- `POST /api/location/browser` — ブラウザ Geolocation API から座標を受け取り保存する

#### パス品質ランク定義
| ランク | 最大仰角 | 表示色 |
|--------|----------|--------|
| excellent | 60度以上 | 緑 (#2ecc71) |
| good | 30度以上60度未満 | 青 (#3498db) |
| fair | 10度以上30度未満 | 黄 (#f1c40f) |
| low | 10度未満 | グレー (#95a5a6) |

### グラフィカルパス予測表示 (src/ui/)
- `PassChartView` (src/ui/pass_chart.py): PySide6 + QtCharts ウィジェット
  - 横軸: 時刻（AOS〜LOS）、縦軸: 仰角（0〜90度）
  - 各パスをサイン近似の山型曲線で描画
  - 品質ランクで色分け
  - 現在時刻を赤い縦線で表示
  - パスクリック時に詳細情報を `pass_clicked` Signal で通知
- `pass_chart.js` (src/web/static/pass_chart.js): Chart.js によるブラウザ向け同等実装
  - `renderPassChart(canvasId, passes, satName)` — キャンバスにチャート描画
  - `fetchAndRenderPasses(canvasId, noradId, satName, options)` — APIから自動取得して描画
  - `showPassDetail(pass)` — クリック時の詳細ポップアップ

### レーダーチャート（スカイビュー）(src/ui/, src/web/static/)

#### デスクトップ版 (src/ui/radar_view.py)
PySide6 の QPainter で以下を実装:
- 円形レーダー表示（同心円で仰角 0/30/60/90 度を表示）
- 上が北固定（North-up）
- 衛星の現在位置をドットで表示（衛星名ラベル付き）
- パスの軌跡を曲線で描画（AOS から LOS まで）
- AOS/LOS の時刻をパス線の端に表示
- 現在仰角を下部に数値表示（例: "EL: 34.2°  AZ: 247.5°"）
- 複数衛星を色分けして同時表示
- `SatTrackData` データクラス: name, norad_cat_id, azimuth_deg, elevation_deg, is_visible, track, aos_time, los_time
- `az_el_to_xy(az, el, cx, cy, r)` — 方位角・仰角をレーダー上の (x, y) に変換するユーティリティ
- `sat_clicked(str)` Signal — 衛星ドットクリック時に衛星名を emit

#### ブラウザ版 (src/web/static/radar.js)
Canvas API で同等のレーダー表示:
- `RadarView` クラス: `new RadarView('canvasId')` でインスタンス化
- `setTracks(tracks)` — 衛星データ配列を設定して描画
- スマホでは `DeviceOrientationEvent` で方位を取得してレーダーを自動回転（コンパス連動）
- 方位センサーがない場合は北固定にフォールバック
- タッチ/クリックで衛星をタップすると `onSatClick(track)` コールバックを呼ぶ
- `azElToXY(az, el, cx, cy, r, rotationDeg)` — 座標変換ユーティリティ（公開関数）

### 自局位置の自動取得 (src/core/location.py)

取得優先順位:
1. GPS デバイス（gpsd デーモン経由 / python-gps）
2. ブラウザ Geolocation API（POST /api/location/browser 経由）
3. IPジオロケーション（ip-api.com）
4. 手動入力（緯度・経度・標高 / QTH グリッドロケーター形式）

主要コンポーネント:
- `LocationSource` enum: `GPS` / `Browser` / `IP` / `Manual`
- `Location` dataclass: latitude_deg, longitude_deg, elevation_m, source, accuracy_m, city, country
- `grid_to_latlon(grid: str) -> tuple[float, float]` — Maidenhead グリッドロケーターを緯度経度に変換
- `LocationManager` クラス:
  - `detect()` — 優先順位に従って自動取得（async）
  - `from_gps()` — gpsd 経由で GPS 座標取得（async）
  - `from_ip()` — ip-api.com で IP ジオロケーション（async）
  - `from_manual(lat, lon, elev)` — 手動設定
  - `from_grid(grid, elev)` — グリッドロケーターから設定
  - `set_browser_location(lat, lon, accuracy_m)` — ブラウザ位置を設定
  - `save(loc)` — app_settings に保存
  - `load_saved()` — 保存済みを読み込む
  - `status_text` プロパティ — ステータスバー表示テキスト（例: "QTH: 35.6895°N 139.6917°E (GPS)"）

### i18n (src/i18n/)

#### 設計方針
- Python 標準 `gettext` ベース。外部ライブラリ不要
- 翻訳ドメイン: `gpredict_improved`
- 翻訳ファイル: `locale/<lang>/LC_MESSAGES/gpredict_improved.{po,mo}`
- 新言語の追加は `.po` ファイルを追加して `msgfmt` でコンパイルするだけ

#### 公開 API

```python
from i18n import _, ngettext, set_language, get_language, available_languages

set_language("ja")          # 言語を変更（スレッドセーフ）
get_language()              # 現在の言語コードを返す → "ja"
available_languages()       # 利用可能な言語一覧 → ["en", "ja"]
_("Ready")                  # 翻訳 → "準備完了"
ngettext("%(n)d satellite", "%(n)d satellites", n)  # 複数形対応
```

#### 重要な規則
- `set_language()` は **Qt UI の設定変更時のみ**呼ぶ。起動時はシステムロケールを参照する予定
- `from i18n import _` してから `set_language()` を呼んでも、`_()` は常に最新のカタログを参照する（関数オブジェクトはモジュールの `_translation` グローバルを参照するため）
- `.mo` ファイルはコンパイル済みバイナリ。`.po` ファイルを編集したら必ず `msgfmt` で再コンパイルしてコミットする
- `locale/` はプロジェクトルート直下に配置（`src/` の外）

#### 翻訳対象
- UI テキスト全般（メニュー・ボタン・ラベル・ステータスメッセージ）
- エラーメッセージ（ユーザー向けのもの）
- 翻訳不要: ログ出力・コード内定数・NORAD IDなどのデータ値

---

## 外部API仕様

### SATNOGS API
- Base URL: `https://db.satnogs.org/api/`
- 認証不要
- `GET /transmitters/?satellite__norad_cat_id={norad}&status=active`
- レート制限: 緩やか（日次更新で十分）

### CelesTrak
- `https://celestrak.org/SOCRATES/query.php?GROUP=amateur&FORMAT=tle`
- 認証不要
- アマチュア衛星: `amateur.txt`
- ISSなど主要局: `stations.txt`

### Space-Track.org（オプション）
- 要アカウント（無料）
- 設定画面でユーザー名/パスワードを入力
- OMM形式対応

---

## Hamlib関連

### 対応デバイス
- Hamlibがサポートする700機種以上の無線機
- 主要なアマチュア衛星対応機: IC-9700, IC-9100, IC-705, FT-991A, TS-2000, FT-817ND

### モードマッピング（SATNOGS → Hamlib）
```python
MODE_MAP = {
    "FM":           Hamlib.RIG_MODE_FM,
    "SSB":          Hamlib.RIG_MODE_USB,   # 衛星SSBは通常USB
    "CW":           Hamlib.RIG_MODE_CW,
    "CW-R":         Hamlib.RIG_MODE_CWR,
    "DIGITALVOICE": Hamlib.RIG_MODE_FM,    # D-STARなど
    "BPSK":         Hamlib.RIG_MODE_PKTUSB,
    "AFSK":         Hamlib.RIG_MODE_PKTFM,
}
```

### Linux USBデバイス権限
- インストール時に `/etc/udev/rules.d/99-gpredict-improved.rules` を配置
- `dialout` グループへの追加を案内

---

## コミットメッセージ規則

形式: `<type>(<scope>): <概要（英語・50文字以内）>`

**type一覧:**

| type | 用途 |
|------|------|
| `feat` | 新機能追加 |
| `fix` | バグ修正 |
| `refactor` | リファクタリング（動作変更なし） |
| `test` | テスト追加・修正のみ |
| `chore` | 設定・ビルド・CI等 |

**scope一覧:**

| scope | 対象 |
|-------|------|
| `rig` | リグ制御関連 |
| `data` | DB・TLE・SatNOGS関連 |
| `ui` | Qt UIコンポーネント |
| `core` | 軌道計算・ドップラー計算 |
| `web` | WebサーバーAPI |
| `ci` | GitHub Actions |

**例:**
```
feat(rig): add set_vfo_frequencies for stable FTX-1 VFO control
fix(rig): resolve chk_vfo timeout disconnecting socket
feat(data): add SatNOGS type mapping in sync_from_satnogs
test(rig): add coverage for VFO sequence and timeout handling
```

## コミット後のプッシュ規則

**コミット直後に必ずpushすること。** 理由：
- CIの早期確認
- 作業内容のバックアップ
- コンテキスト引き継ぎ時の最新状態保証

コミットのみでpushを忘れた場合は、次のアクション前に必ずpushする。

---

## テスト方針

- `tests/` 以下にpytest
- ネットワーク不要なテストは積極的に書く（Hamlibはモック）
- TLE計算・ドップラー計算は既知の値でリグレッションテスト
- CI（GitHub Actions）でLinux/Windows/macOS全プラットフォームでテスト実行

---

## ビルド・配布

- **Linux**: AppImage（全distro対応）+ `.deb`（Ubuntu/Debian）
- **Windows**: PyInstaller → NSIS インストーラー `.exe`
- **macOS**: PyInstaller → `.dmg`
- **GitHub Actions**: タグpushで3プラットフォーム自動ビルド → GitHub Releases

---

## 開発環境セットアップ（Ubuntu）

```bash
# システム依存パッケージ
sudo apt install python3.11 python3.11-venv python3-pip \
    libhamlib-dev python3-hamlib \
    qt6-base-dev libqt6webkit6-dev \
    pkg-config cmake

# Python仮想環境
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# udevルール（USB無線機アクセス）
sudo cp scripts/99-gpredict-improved.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo usermod -aG dialout $USER
```

---

## 重要な設計判断

1. **手動追加トランスポンダはSATNOGSより優先**: `manual_override=True` のレコードはSATNOGS同期時に上書きされない
2. **オフライン動作を保証**: すべてのデータはローカルSQLiteにキャッシュ。ネットワーク不要で起動・動作可能
3. **初心者ファースト**: デフォルト設定で「インストールして起動するだけ」で動作。高度な設定はオプション
4. **GPredict互換性**: NET Controlモードで従来のrigctld/rotctldとの互換性を維持
5. **マルチプラットフォーム**: OS固有コードを最小化。プラットフォーム分岐は `src/core/platform.py` に集約

---

## 実装済み機能一覧（2026年5月18日時点）

- 衛星追尾エンジン（Skyfield）
- Qt6デスクトップUI（世界地図・レーダー・Pass Chart・Radio Control）
- FastAPI内蔵Webサーバー（ポート8080）
- スマホブラウザUI（グループフィルター・Favorites・Group Pass・レーダー）
- Hamlib内蔵リグ制御（Direct/NET Control）
- SATNOGS周波数DB同期・手動追加
- TLE自動更新（CelesTrak: Amateur/CubeSat/Weather/Earth-Obs/Science/Stations）
- AMSAT運用状況スクレイピング・色分け表示
- お気に入り機能（デスクトップ・スマホ共通DB）
- フットプリント表示
- Upcoming Passes（Target/Groupタブ・カレンダー選択・CSV出力）
- 409テスト全パス・CI緑

## 次回の作業候補
1. ドップラー補正の実動作確認
2. ローテーター設定ダイアログ
3. AppImageビルド（配布パッケージ）

---

## HamlibNetController 実装メモ（2026-05-20 確認済み）

### FTX-1F + rigctld での動作確認済みプロトコル

**接続時（1回のみ）:**
  S 1 Main → RPRT 0

**毎サイクル（1秒間隔）:**
  F {dl_hz} → RPRT 0  （前回から1Hz以上変化した場合のみ）
  I {ul_hz} → RPRT 0  （前回から1Hz以上変化した場合のみ）

### FTX-1F固有の制約（Hamlibバックエンドが吸収）
- S 1 Main 応答に約150ms かかる
- F/I コマンド応答は約150ms
- f/i（get_freq）コマンドはF/I送信直後に10秒以上かかる → 使用禁止
- アクティブVFO切り替え（V コマンド）はTX点灯を引き起こす → 使用禁止

### 実装上の重要事項
- set_vfo_frequencies()はバックグラウンドスレッドで実行（UIブロック防止）
- _cmd()はソケットタイムアウト10秒
- connect()時に_last_dl_hz/_last_ul_hzをNoneにリセット
- f/iダイアルフィードバックは実装しない（FTX-1非対応）
- S 1 Mainは接続時1回のみ（毎サイクル送らない）

### 動作確認環境
- リグ: Yaesu FTX-1F
- PC: GPD MicroPC2 (Ubuntu)
- Hamlib: 4.7.1-rc (2026-02-16) モデルID 1051
- 接続: USB → /dev/FTX1CAT → udev/systemd → rigctld:4532
