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
- **コメント言語**: すべてのコードコメント（`#` 行コメント・docstring）は**英語**で書くこと。日本語コメントは使用しない。

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

### ローカル実行の注意（GPD MicroPC2）

**`pytest tests/` による全テスト一括実行はシステムをフリーズさせる可能性がある。**
`test_main_window.py` の実行も同様にフリーズする。

ローカルでは **`test_rig.py` のみ** を実行すること：
```bash
python -m pytest tests/test_rig.py -q 2>&1 | tail -5
```

`test_main_window.py` のテストは CI（GitHub Actions）で確認する。

### コミット前チェックリスト

**必ずこの順番で実行すること。いずれかが失敗したらコミットしない。**

```bash
# 1. フォーマット（自動修正）
ruff format src/ tests/

# 2. リントチェック
ruff check src/ tests/

# 3. テスト（test_rig.pyのみ）
python -m pytest tests/test_rig.py -q 2>&1 | tail -5
```

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

## 実装済み機能一覧（2026年6月7日時点）

- 衛星追尾エンジン（Skyfield）
- Qt6デスクトップUI（**Dashboard**・世界地図・レーダー・Pass Chart・Group Pass Chart・Radio Control）
- FastAPI内蔵Webサーバー（ポート8080）
- **スマホブラウザUI**（グループフィルター・Favorites・Group Pass・レーダー・Antenna タブ）
- Hamlib内蔵リグ制御（Direct/NET Control）・Rig 1 / Rig 2 デュアルリグ対応
- SATNOGS周波数DB同期・手動追加
- **コミュニティ周波数DB**（`src/data/community_transmitters.json`）— FT4コーリング周波数など、SATNOGSにない慣習周波数を `source='community'` として管理。SATNOGS同期で上書きされない
- TLE自動更新（CelesTrak: Amateur/CubeSat/Weather/Earth-Obs/Science/Stations）
- **SATNOGS仮ID（90000番台）衛星のTLE自動取得・仮ID→実ID移行パイプライン**
- **超古い衛星（NORAD < 10000）の一括チェック：CelesTrak 未収録なら自動非表示**
- AMSAT運用状況スクレイピング・色分け表示
- **カスタムFavoriteグループ**（Favorite 1/2/3 デフォルト、Settings > Custom Groups で追加/削除/改名可能）
- **フットプリント表示**（スキャンライン方式・極地域対応・ズーム地図との座標整合済み）
- Upcoming Passes（Target/Groupタブ・カレンダー選択・CSV出力）
- **Group Pass Chart** — グループ検索結果を衛星別カラーで描画（ホバーでツールチップ表示）
- カレンダーポップアップ改善（英語ロケール固定・週番号列非表示・To欄はCurrent Timeボタンなし）
- **AOS/LOS デスクトップ通知**（Linux: notify-send / macOS: osascript / Windows: plyer+PowerShell）
  - Settings > Notifications タブ: AOS通知ON/OFF・何分前か・LOS通知ON/OFF
  - Target衛星・Group検索結果の両方に対応
- **Autotrack（自動順次追尾）**（src/core/autotrack.py）
  - Settings > Autotrack Lists タブ: リスト作成・衛星＋トランスポンダー登録・並び替え
  - Radio Control に Autotrack セクション追加（リスト選択・Enable チェック・ステータス表示）
  - 切り替えロジック: 現在衛星が Min El 以下になったら次の衛星へ自動切替
  - 同一AOS時はリスト順優先、パス途中の切り替えなし
  - 使い方説明ツールチップ（?ボタン）付き
- **CPU負荷最適化**
  - 世界地図更新を5秒ごとに変更（毎秒→5秒）
  - `_visible_norads`（フィルター表示中の衛星のみ）で Skyfield 計算
  - `_sat_name_cache` で毎秒の DB SELECT を排除
  - `_last_elevations` で仰角データを Autotrack と共有
- Radio Control レイアウト縦幅圧縮（Name/NORAD・DL/Doppler・UL/Doppler・Mode/CTCSS・AZ/EL を各1行に）
- **スマホ Web UI 大幅強化**（Antenna タブ・コンパス切り替え・RIG 遠隔制御）
- **Dashboard タブ**（src/ui/dashboard_view.py）— ズームマップ＋レーダー＋ステータスバーの統合ビュー
  - Dashboard 表示中は Satellite Detail パネルを自動非表示
  - ズームマップはグリッド線・赤道線を非表示（WorldMapView.set_show_grid(False)）
  - NASA Topographic 1024px をデフォルト世界地図として採用（初回起動時に自動ダウンロード）
  - 速度予測ズームセンター（1Hz差分速度 × 3秒先読み + lerp 0.25）でスムーズ追尾
  - 速度スパイクガード: 0.15°/s 超の推定速度は衛星位置にスナップして暴走防止
- **World Map 衛星ドットクリック選択**（`sat_clicked(int)` シグナル → `_select_satellite_by_norad` 接続）
- **フットプリント描画 QPainterPath スキャンライン方式**（polar cap・antimeridian・極境界弧の全ケース修正済み）
- **MainWindow `_shutdown_flag`（threading.Event）**: `closeEvent()` 冒頭でセット。バックグラウンドスレッド（`_refresh_satellite_names_sync`）が各 `asyncio.run()` 呼び出しの間でフラグを確認し、インタプリタシャットダウン後の `futures` スケジュールを防ぐ
- **`is_source_stale(source_name)` (TLEManager)**: `sync_log` を照会し、一度もフェッチされていないソース（`never-fetched`）を検出。初回起動時に cubesat/weather/science/earth-obs グループを即時フェッチするトリガーとして使用
- **`_sort_sources_by_priority()` (MainWindow)**: TLE_SOURCES の `priority` フィールドでソース名を昇順ソート。amateur より先に cubesat/weather 等を上書きしないよう順序を制御
- **GitHub Actions: `make_latest: true`**（`prerelease: true` を廃止）。3プラットフォーム全ビルドジョブで設定済み。最新リリース: `v0.1.0-beta.29`
- CI緑（mypy strict + pytest）

### カスタムFavoriteグループ設計（src/data/database.py）

```sql
CREATE TABLE custom_groups (
    id          INTEGER PRIMARY KEY,  -- 1-based group number
    name        TEXT NOT NULL,        -- display name (e.g. "Favorite 1")
    sort_order  INTEGER NOT NULL DEFAULT 0
);
-- satellites テーブルに favorite_group INTEGER DEFAULT 0 カラムを追加
-- 0=未所属, 1..N=custom_groups.id
```

- デフォルトで Favorite 1/2/3 を作成（既存 is_favorite=1 は Favorite 1 に移行）
- 右クリック → 「★ Favorite Groups」サブメニューでグループ割当・解除
- Settings > Custom Groups タブでグループ名インライン編集・追加・削除

### コミュニティ周波数（src/data/community_transmitters.json）

| 衛星 | Rx (DL) | Tx (UL) | Mode | 出典 |
|------|---------|---------|------|------|
| RS-44 (NORAD 44909) | 435.612 MHz | 145.993 MHz | FT4 | JH1NHK |
| JO-97 (NORAD 43803) | 145.857 MHz | 435.118 MHz | FT4 | JH1NHK |
| MO-122 (NORAD 60209) | 435.812 MHz | 145.938 MHz | FT4 | JH1NHK |

### Dashboard タブ（src/ui/dashboard_view.py）

左2/3にズームマップ、右1/3にレーダー、下部に36pxのステータスバーを配置した統合ビュー。

#### レイアウト構造

```
┌─────────────────────────────┬──────────────┐
│  WorldMapView（ズーム）      │  RadarView   │
│  （2/3 幅）                 │  （1/3 幅）  │
├─────────────────────────────┴──────────────┤
│  ステータスバー（36px固定）                  │
│  衛星名 / EL / AZ / Range / 可視 / DL / UL │
└────────────────────────────────────────────┘
```

#### 主要な設計判断

- `QSplitter` で左右を分割。`setStretchFactor(0,2) / setStretchFactor(1,1)` + `setSizes([660, 330])` で初期2:1比率を強制
- Dashboard 表示時は Satellite Detail パネルを非表示: `currentChanged` ではなく起動時に `setVisible(False)` で初期化（`currentChanged` は初期タブでは発火しないため）
- ズームマップはグリッド線を非表示（`set_show_grid(False)`）— 衛星移動に伴う線のカクカク感を回避
- `isVisible()` チェックで非表示時の再描画をスキップ（CPU負荷削減）
- `track_data: SatTrackData | None` パラメータで Radar タブと同一のパス軌跡を表示
- レーダーの AOS/LOS 時刻表示は `set_use_utc()` で UTC/Local 切り替えに連動

#### WorldMapView への追加 API（src/ui/world_map.py）

| メソッド | 説明 |
|---|---|
| `set_show_grid(show: bool)` | グリッド線・赤道線の表示/非表示を切り替え |
| `set_zoom_region(lat, lon, span_deg)` | 指定座標を中心にズーム表示（デフォルト ±50°） |
| `clear_zoom()` | グローバルビューに戻す |

#### フットプリント描画の設計（`_draw_footprint` — src/ui/world_map.py）

**スキャンライン QPainterPath 方式**（ポリゴン方式から変更済み）:
- N=180 ラチチュードバンドを走査し、各バンドを `QRectF` として `QPainterPath` に追加
- `QPainterPath.setFillRule(Qt.FillRule.WindingFill)` で確実に全領域を塗りつぶし（OddEven 規則のワインディングキャンセル問題を回避）
- 緯度ごとに球面余弦定理で経度半幅 `dlon` を計算
- `cos(rho) = sin(lat0)*sin(lat) + cos(lat0)*cos(lat)*cos(dlon)` を解く
- `is_full_width[i]` フラグ: `dlon ≥ 180°` の行は極域を包む全経度帯 → `xl=0, xr=w` を直接設定
- Antimeridian（日付変更線）越え: `xl > xr` の行は左端・右端の2つの `QRectF` に分割
- fill: `rgba(100,200,255,140)`、outline: シアン `#00DCFF` 3px（陸地・雪氷上でも視認可）

**アウトラインスキップ規則（重要）**:
- `is_full_width[i] and is_full_width[i+1]` のペアのみスキップ（両端が全幅行 → 画面端の縦線を防ぐ）
- `is_full_width[i] or is_full_width[i+1]`（`or`）は使用禁止 → 極境界の弧が開いてしまう
- 水平幅 `abs(x2 - x1) < w/3` のセグメントのみ描画（日付変更線越えの大ジャンプを除外）
- 極冠境界（normal↔full_width 遷移）はスキップせず描画し、弧の閉合を確保

**ズームモードの座標整合（重要）**:
- `latlon_to_xy` は地図画像描画と同じクランプ済みlatレンジを使用する
- 地図描画: `lat_max = min(90, clat+span)` でクランプ → 実際のスパンが `2*span` より小さくなる
- オーバーレイ（衛星ドット・フットプリント）も同じ計算を使わないと、極地域で南方向にずれて見える
- `rendered_lat_span = min(90,clat+span) - max(-90,clat-span)` で y を正規化

**衛星ドットクリック**:
- `mousePressEvent` で衛星ドット中心12px以内のクリックを検出し `sat_clicked(int)` を emit
- `main_window.py` で `_world_map.sat_clicked.connect(self._select_satellite_by_norad)` に接続済み

#### デフォルト世界地図（NASA Topographic 1024px）

- `settings_dialog.get_world_map_path()`: 明示的な選択がない場合、`nasa-topo_1024.jpg` が存在すればそのパスを返す
- `main_window._apply_world_map()`: 初回起動時（ファイル未存在）はバックグラウンドスレッドで GPredict リポジトリから自動ダウンロード。完了後 `QMetaObject.invokeMethod` で再適用

### Group Pass Chart（src/ui/pass_chart.py — GroupPassChartView）

- Group タブで検索実行後に自動表示（それまでタブ非表示）
- 衛星ごとに12色パレットから自動割り当て（>12衛星は循環）
- 凡例は非表示。マウスホバーでツールチップ（衛星名＋最大仰角）
- Range選択: 4h / 8h / 12h / 24h（Target Pass Chartと同じ）
- UTC/Local 切り替えに連動

### Autotrack 設計（src/core/autotrack.py）

```sql
CREATE TABLE autotrack_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE autotrack_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id       INTEGER NOT NULL REFERENCES autotrack_lists(id) ON DELETE CASCADE,
    norad_cat_id  INTEGER NOT NULL,
    xpdr_uuid     TEXT NOT NULL,   -- 使用するトランスポンダーのUUID
    sort_order    INTEGER NOT NULL DEFAULT 0,
    notes         TEXT DEFAULT ''
);
```

#### 切り替えロジック（AutotrackManager.check()）
1. 現在衛星が Min El 以上 → 継続追尾
2. 現在衛星が Min El 以下:
   a. 別の衛星がすでに可視 → 即座に切り替え（リスト順タイブレーク）
   b. 可視衛星なし → AOS が最も近い衛星に切り替え（リスト順タイブレーク）
3. パス途中は切り替えしない（LOS を待つ）

#### 使用前提条件
1. Settings > Autotrack Lists でリスト作成・衛星登録
2. Upcoming Passes > Group タブでパス検索実施
3. Radio Control でリスト選択 → Enable Autotrack をオン

### スマホブラウザ Web UI（src/web/static/）

#### タブ構成
| タブ | 内容 |
|---|---|
| **Tracking** | 衛星リスト・EL/AZ・Range・レーダー |
| **Antenna** | AZ/EL 大表示・周波数・トランスポンダー選択・RIG接続 |
| **Pass Prediction** | パス予測一覧 |
| **Group Pass** | グループ検索・パス一覧 |

#### Antenna タブ（手動アンテナ追尾用途に特化）

想定ユースケース: スマホで AZ/EL を見ながら八木アンテナを手動で向け、PCでドップラー補正する運用

| 機能 | 詳細 |
|---|---|
| **AZ/EL 大表示** | 42px の大きな数字でリアルタイム表示 |
| **パス進行バー** | AOS〜LOS の進行状況（緑バー）+ LOS カウントダウン |
| **周波数（読み取り専用）** | Doppler 補正済み DL/UL 周波数とシフト量を表示 |
| **トランスポンダーカードリスト** | 衛星選択時に自動取得・カード形式で表示・タップで選択 |
| **Connect/Disconnect RIG** | スマホからリグ接続をトリガー（設定はPC側で事前設定が必要） |
| **RIG ON/OFF ボタン** | 接続済みリグの Doppler 補正 ON/OFF |
| **ROT ON/OFF ボタン** | ローテーター接続時のみ表示 |

#### コンパス連動（レーダー North-Up / Compass 切り替え）

- レーダー画面右上の「N↑ North Up」ボタンで切り替え
- **Android**: HTTP でも動作（即時切り替え）
- **iOS 16+**: HTTP では `DeviceOrientationEvent` が無効（Apple のセキュリティ制限）。HTTPS が必要
- **iOS 13–15**: 許可ダイアログ後に使用可能

#### RIG 遠隔制御アーキテクチャ（src/web/rig_state.py）

```python
# RigWebState — Qt UI スレッド（書き込み）と FastAPI スレッド（読み込み）の共有状態
rig_connected: bool      # Rig 1 接続状態
rig_engaged: bool        # Doppler 補正動作中
dl_hz / ul_hz: float    # 補正済み周波数
rig_connect_requested    # POST /api/rig/connect でセット → Qt が処理して接続
rig_disconnect_requested # POST /api/rig/disconnect でセット → Qt が処理して切断
```

**WebSocket ペイロード拡張**（`/ws/tracking` レスポンスに追加）:
```json
{
  "rig": { "connected": true, "engaged": true, "dl_hz": 435611234, "mode": "SSB" },
  "rot": { "connected": false }
}
```

**REST エンドポイント**:
- `POST /api/rig/connect` `{norad, xpdr_uuid}` — 衛星・トランスポンダー選択＋接続
- `POST /api/rig/disconnect` — 切断
- `POST /api/rig/toggle` — Doppler ON/OFF トグル
- `POST /api/rot/toggle` — ローテーター ON/OFF トグル

## 次回の作業候補

### 正式版リリース前に必須
0. **デバッグ用ログファイル出力の削除または設定化** — `src/main.py` の `_setup_logging()` にある frozen バンドル（dmg/exe/AppImage）向けのファイルログ出力（`platformdirs.user_log_dir` にログファイルを書き出す処理）は、dmgビルドのデバッグ目的で追加したもの。正式版リリース前に以下のいずれかを選択して対処すること:
   - **削除**: frozen バンドル時のファイルハンドラー追加・`print` 出力・`platformdirs` インポートを削除し、stderr のみに戻す
   - **設定化**: Settings に「デバッグログを保存する」チェックを追加し、デフォルト OFF にする
   - 該当箇所: `src/main.py` 63〜75行目（`if getattr(sys, "frozen", False):` ブロック）

### 継続中・優先度高
1. **ドップラー補正の実動作確認** — 実機（FTX-1F / FT-991A）での実衛星追尾テスト
2. **ローテーター設定ダイアログの改善** — 接続テストボタン・AZ/ELリミット設定

### モバイル・Web UI
3. **スマホ・タブレット画面の継続確認** — Android 実機でのコンパス連動確認、各種ブラウザでの表示確認

### SDR・デジタルモード
4. **SDR機能の追加（段階的）**
   - フェーズ1: SSB・CWソフトウェアデモジュレーター（Pythonモジュール）
   - フェーズ2: SSTV / SSDV デコーダー
   - フェーズ3: APRS デコーダー・位置情報表示
   - 各フェーズは独立した拡張モジュールとして追加

### 配布・ビルド
5. **Windows・macOS AppImage / インストーラー作成試験** — GitHub Actions CI でのクロスプラットフォームビルド検証

### データ・連携
6. **観測ログ機能** — 実際に追尾・通信した衛星パスを記録・集計・エクスポートする機能
7. **多言語対応（日本語）** — フェーズ2として日本語UIの追加（翻訳ファイルは準備済み）

### ハードウェア連携
8. **追加リグの実機テスト** — IC-9700・TS-2000・FT-817ND 等でのドップラー制御動作確認（satmode含む）
9. **WSJT-X / JS8Call 連携** — デジタルモード運用ソフトとの周波数・モード連動（将来）

---

## 多言語化ロードマップ

### 開発方針
**フェーズ1（現在）**: 英語モードのみで全機能を完成させる。  
**フェーズ2（英語完成後）**: 日本語モードを追加する。

コード中のすべての UI 文字列は `_("...")` でラップ済みであること。
新しい文字列を追加する際も必ず `_("English string")` で書くこと（日本語をハードコードしない）。

### 日本語モード追加時の作業手順

#### 1. 翻訳対象文字列の抽出
```bash
# src/ 以下の _("...") を全て抽出して .pot ファイルを生成
xgettext --language=Python --keyword=_ --keyword=ngettext:1,2 \
    -o locale/gpredict_improved.pot \
    $(find src/ -name "*.py")
```

#### 2. 日本語 .po ファイルの作成・更新
```bash
# 初回: テンプレートから ja.po を作成
msginit --input=locale/gpredict_improved.pot \
        --locale=ja --output=locale/ja/LC_MESSAGES/gpredict_improved.po

# 2回目以降: 既存 .po に新しい文字列をマージ
msgmerge --update \
    locale/ja/LC_MESSAGES/gpredict_improved.po \
    locale/gpredict_improved.pot
```

#### 3. .po ファイルの翻訳編集
`locale/ja/LC_MESSAGES/gpredict_improved.po` を開き、
`msgstr ""` の部分に日本語訳を記入する。

```po
# 例
msgid "RIG: Off"
msgstr "RIG: 切断"

msgid "RIG: On"
msgstr "RIG: 接続中"

msgid "Satellite Position"
msgstr "衛星位置"
```

#### 4. .mo ファイルのコンパイル
```bash
msgfmt locale/ja/LC_MESSAGES/gpredict_improved.po \
       -o locale/ja/LC_MESSAGES/gpredict_improved.mo
```

#### 5. 動作確認
```python
# 起動時またはメニューから言語切り替え
from i18n import set_language
set_language("ja")
```

#### 6. コミット対象
`.po` と `.mo` の両方をコミットする（`.mo` はバイナリだが配布に必要）。

### 注意事項
- `_("...")` の中身は**常に英語**で書く（gettext の msgid が英語前提）
- Qt 標準ダイアログ（QMessageBox等）のボタン文字列は Qt 側の翻訳ファイル（`qtbase_ja.qm`）が担当するため別途対応不要
- Web UI（`src/web/static/`）の JavaScript 文字列は別管理（gettext 非対応）。フェーズ2では手動置換またはブラウザ向け i18n ライブラリの導入を検討する

---

## HamlibRotatorController — Catch-up タイムアウト設計

### 仕組み
接続直後の初回 `set_position()` 呼び出し時、ローテーターは現在位置から
目標 AZ/EL へ向かって動き始める（**catch-up フェーズ**）。
この間、毎サイクル `get_position()` でローテーターの実位置を確認し、
目標との差が **5 度以内**になった時点で通常追跡（毎サイクル P コマンド送信）に移行する。

### タイムアウト再送信
低速なローテーター（AZGTI 等）や衛星と同方向移動中など、
5 度以内に収束しないまま時間が経過するケースがある。
`_CATCH_UP_TIMEOUT = 60.0`（秒）を超えても catch-up が終わらない場合は、
現在の衛星 AZ/EL を改めて P コマンドで送信してタイマーをリセットする。
これにより、ローテーターが古い目標位置に向かって動き続ける問題を回避する。

### 定数（src/rig/controller.py — HamlibRotatorController）
| 定数 | 値 | 意味 |
|---|---|---|
| `_CATCH_UP_THRESHOLD` | 5.0 度 | この差以内になったら通常追跡へ移行 |
| `_CATCH_UP_TIMEOUT` | **60.0 秒** | この時間を超えたら P コマンドを再送信 |

---

## HamlibNetController 実装メモ（2026-05-20 確認済み）

### rigctld 標準プロトコルと VFO 割り当て（全機種共通）

**接続時（1回のみ）:**
  S 1 Main → RPRT 0  （split ON。Main=RX(DL) / Sub=TX(UL) を確立）

**毎サイクル（1秒間隔）:**
  F {dl_hz} → RPRT 0  （Main=RX / ダウンリンク周波数。前回から1Hz以上変化した場合のみ）
  I {ul_hz} → RPRT 0  （Sub=TX / アップリンク周波数。前回から1Hz以上変化した場合のみ）

**VFO 割り当ての原則（Hamlib 全機種共通）:**
- `S 1 Main` 送信後: **Main = RX（ダウンリンク）、Sub = TX（アップリンク）**
- `F {hz}`: Main VFO（RX/ダウンリンク）の周波数を設定
- `I {hz}`: Sub VFO（TX/アップリンク）の周波数を設定（split TX）
- 各バックエンドがこの割り当てを実現する仕組みはリグ固有だが（下記参照）、結果は全機種共通

**各リグでの実現メカニズム（Hamlib ソースで確認済み）:**
| リグ | S 1 Main の動作 |
|------|----------------|
| FTX-1F | バックエンドが S コマンド引数に関わらず Main=RX を強制 |
| IC-9700 | `S 1 Main`（tx_vfo=Main）が satmode を自動 ON → satmode 時は常に Main=RX, Sub=TX |
| FT-991A | 標準 split 動作: Main(VFOA)=RX, Sub(VFOB)=TX（実機確認済み） |
| その他 Hamlib 対応機 | 同様の split 動作で Main=RX, Sub=TX |

### FTX-1F 固有の制約（Hamlib バックエンドが吸収）
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

### send_mode_only() VFO順序の根拠

全 Hamlib 対応機共通の VFO 割り当て: Sub=TX(UL), Main=RX(DL)

send_mode_only()の正しい順序:
```
V Sub  → M {ul_mode} 0  （Sub=TX=アップリンク）
V Main → M {dl_mode} 0  （Main=RX=ダウンリンク）
```

S 1 Mainはrigctld標準プロトコル。全機種共通。

### 動作確認環境
- リグ: Yaesu FTX-1F
- PC: GPD MicroPC2 (Ubuntu)
- Hamlib: 4.7.1-rc (2026-02-16) モデルID 1051
- 接続: USB → /dev/FTX1CAT → udev/systemd → rigctld:4532

---

## Rig-Specific Implementation Notes

### FTX-1F (Hamlib model 1051)
- rigctld backend forces Sub=TX, Main=RX regardless of S command argument (FTX-1F specific quirk; other rigs achieve the same result through standard split or satmode mechanisms)
- `S 1 Main` is required for split (not `S 1 Sub`) — rigctld standard protocol, universal across all rigs
- `F {hz}` → Main (RX/DL),  `I {hz}` → Sub (TX/UL) via rigctld — universal VFO assignment
- Mode setting: `V Sub → M {ul_mode} 0 → V Main → M {dl_mode} 0` via independent socket
- `V` (active VFO switch) command causes TX LED to light → forbidden in Doppler cycle
- CTCSS: Hamlib `L CTCSS_TONE` → `RPRT -11` (not supported by backend)
  Custom CAT via rigctld `w` command: `CN10{tone:03d};CT11;` / `CT10;`
  `CN P1=1:SUB, P2=0:CTCSS, P3=tone index 000-049`

### FT-991A (Hamlib model 1036, CAT ID=0670)
- `MD` command only targets Main VFO (`P1=0` is fixed)
- VFO-B mode setting requires SV swap: `SV; → MD0{code}; → SV;`
- Hamlib `set_mode(RIG_VFO_B)` → `-11 Feature not available`
- CTCSS: Hamlib `L CTCSS_TONE` → `RPRT -11` (not supported by backend)
  Custom CAT: `CN00{tone:03d};CT02;` / `CT00;`
  `CN P1=0:fixed, P2=0:CTCSS, P3=tone index 000-049`
  `CT P2=2`: CTCSS ENC only; `CT00;` to disable
- rigctld `w CN…` works but requires FM mode to be active on the rig
- `SV`/`MD` commands via rigctld `w` each take ~2 s (wait for RPRT with 2 s timeout)
- `send_mode_only()` runs in a background thread to prevent UI freeze

### NET mode (rigctld) vs Direct mode (Hamlib built-in)
- FTX-1F: both NET and Direct work; NET preferred (more stable)
- FT-991A: both NET and Direct work
  - Direct: `set_mode(RIG_VFO_B)` fails → uses `os.open()` raw serial writes for SV swap
  - NET: uses independent socket for mode/CTCSS commands to avoid Doppler cycle conflict
- Detection: use `ctcss_method` setting value (`"ft991"`) — never use `w ID;` (causes 10 s timeout)

---

## 仮NORAD ID（90000番台）衛星のTLE・トランスポンダー管理

### 背景

SATNOGS は正式 NORAD ID が未確定の衛星に 90000 番台の仮 ID を割り振る。
これらは CelesTrak グループフェッチでは TLE が取得できず、位置が表示されない。

### TLE 取得方法（src/data/tle_manager.py）

SATNOGS TLE API エンドポイントを使用：
```
GET https://db.satnogs.org/api/tle/?norad_cat_id={fake_id}&format=json
```

このエンドポイントは仮 ID に対して以下の3種類のいずれかを返す：
| tle_source | line1 の NORAD | 意味 |
|---|---|---|
| Space-Track.org | 実 NORAD ID | SATNOGS が内部で実 ID を把握 |
| CelesTrak (supplemental) | 実 NORAD ID | CelesTrak 補完カタログで解決 |
| SatNOGS Team | 仮 ID | 独自生成TLE（精度低め・更新頻度低） |

`fetch_provisional_tles()` は `is_hidden=0 AND norad_cat_id >= 90000` の全衛星を対象に
このAPIを呼び出し、TLE を `source='satnogs'`, `tle_group='amateur'` として保存する。

- 起動時に `_refresh_satellite_names_sync()` 完了後に自動実行
- APScheduler で 12 時間ごとに定期更新
- `source='manual'` の TLE は絶対に上書きしない

### 仮ID→実ID 移行パイプライン（src/data/transmitter_manager.py）

`_run_migration_pipeline(fake_id, real_id)` — **冪等。何度呼んでも安全。**

実行される手順（各ステップはスキップ条件あり）：
1. 実 ID の satellites レコードを作成（なければ）
2. 実 ID の衛星名が `OBJECT *` 等のプレースホルダーなら SATNOGS 名で上書き
3. TLE を仮 ID → 実 ID へコピー（実 ID 側に manual TLE があればスキップ）
4. トランスミッタを仮 ID → 実 ID へ移行（実 ID 側に既存ならスキップ）
5. `is_favorite` を実 ID にコピー
6. 実 ID 衛星に `satnogs_source_id = fake_id` を記録
7. 仮 ID を `is_hidden = 2`（システム非表示）に設定

#### トリガー
| トリガー | 発火場所 |
|---|---|
| (A) SATNOGS 衛星 API で `norad_follow_id` が設定された | `sync_satellite_names()` |
| (B) SATNOGS TLE API が返す line1 の NORAD が仮 ID と異なる | `fetch_provisional_tles()` |

### `satnogs_source_id` によるシームレスなトランスポンダー同期

移行後も SATNOGS は仮 ID 側でトランスポンダーを管理し続けることがある。
`satellites.satnogs_source_id = fake_id` が設定された実 ID 衛星は、
`sync_from_satnogs()` 内で以下のルーティングが適用される：

```
SATNOGS API に対して satellite__norad_cat_id=fake_id でクエリ
→ 返ってきたトランスポンダーを norad_cat_id=real_id として保存
```

#### 未実装項目（必要性は低いが、将来的な実装を検討すべき）

| 項目 | 内容 |
|---|---|
| **トリガー(C)：GUI手動設定** | 「この衛星の実 NORAD ID は〇〇」とユーザーが GUI から手動指定する機能。トリガー(A)(B) で自動カバーできるケースがほとんどのため現時点では不要。 |
| **フォールバック検知** | SATNOGS 側がトランスポンダーデータを実 ID に移行した場合に `satnogs_source_id` を自動で NULL にリセットする機能。現状では設定されていても実害はなく、SATNOGS が `norad_follow_id` をトランスポンダーに設定した時点で自然に解決される。 |

### 超古い衛星（NORAD < 10000）の自動クリーンアップ（src/data/tle_manager.py）

`fetch_legacy_tles()` — **起動時一回限りのクリーンアップ（以降は高速 no-op）**

対象：`norad_cat_id < 10000 AND is_hidden=0 AND TLEなし` の衛星（最大 21 機）

```
CelesTrak に個別照会（CATNR={norad}&FORMAT=TLE）
  ┌─ TLE 返却あり → まだ軌道上に存在する
  │   source='celestrak', tle_group='legacy' として保存・表示継続
  └─ TLE 返却なし → 軌道離脱済みと判断
      is_hidden=2（システム非表示）に設定
```

- 2回目以降の起動では対象行が 0 件 → 即リターン（API 呼び出しなし）
- `_refresh_satellite_names_sync()` の末尾でプロビジョナルTLEフェッチの後に実行

### ORIGAMISAT-2（NORAD 68795 / 仮 ID 98325）の状態

```
satellites(norad_cat_id=68795):
  is_hidden = 0          ← 表示中
  satnogs_source_id = 98325  ← 仮 ID でトランスポンダーを取得
  alt_names = ["JS1YRU", "FO-126"]
  TLE: source=manual     ← CelesTrakから手動取得・絶対上書きしない

satellites(norad_cat_id=98325):
  is_hidden = 2          ← システム非表示
  transmitters = 0件     ← 全て 68795 に移行済み
```

この衛星は既に最終状態にあり、移行パイプラインは冪等ルールにより何も変更しない。

---

## TLE 取り込みルール全体設計（2026-05-29 確定）

### TLE ソース一覧と優先度

| 関数 | ソース | 対象 NORAD 範囲 | 更新頻度 | source 値 | tle_group 値 |
|---|---|---|---|---|---|
| `fetch_and_update('celestrak-stations')` | CelesTrak STATIONS | ISS・CSS 等 | 1時間ごと | `celestrak` | `stations` |
| `fetch_and_update('celestrak-amateur')` | CelesTrak AMATEUR | アマチュア衛星 | 2時間ごと | `celestrak` | `amateur` |
| `fetch_and_update('celestrak-cubesat')` | CelesTrak CUBESAT | CubeSat | 4時間ごと | `celestrak` | `cubesat` |
| `fetch_and_update('celestrak-weather')` | CelesTrak WEATHER | 気象衛星 | 6時間ごと | `celestrak` | `weather` |
| `fetch_and_update('celestrak-earth-obs')` | CelesTrak RESOURCE | 地球観測 | 12時間ごと | `celestrak` | `earth-obs` |
| `fetch_and_update('celestrak-science')` | CelesTrak SCIENCE | 科学衛星 | 12時間ごと | `celestrak` | `science` |
| `fetch_active_tles()` | CelesTrak(複数グループ)+SATNOGS TLE API | 10000-89999・未収録 | 24時間ごと(起動時stale確認) | `celestrak` or `satnogs` | `amateur`(INSERT時) / 既存保持(UPDATE時) |
| `fetch_provisional_tles()` | SATNOGS TLE API | NORAD ≥ 90000 | 12時間ごと | `satnogs` | `amateur` |
| `fetch_legacy_tles()` | CelesTrak 個別照会 | NORAD < 10000 | 起動時1回のみ | `celestrak` | `legacy` |
| `add_manual_tle()` | ユーザー手動入力 | 任意 | 手動 | `manual` | `amateur` |

### 上書きルール（優先度）

```
manual（最高優先）> celestrak > satnogs > なし
```

- `source='manual'` の TLE は **いかなる自動同期でも上書きしない**
- 既存 TLE が `celestrak` の場合、`satnogs` ソースの取得結果で上書きしない
  （`fetch_provisional_tles()` は `INSERT OR REPLACE` だが `source='manual'` チェックで防御）
- `fetch_active_tles()` の UPDATE では `tle_group` を保持（分類を劣化させない）
- **初回起動時の未フェッチソース自動検出**: `TLEManager.is_source_stale(source_name)` が `sync_log` 未記録のソースを `True` で返す → MainWindow が起動時に未フェッチグループを即時フェッチ
- **フェッチ順序制御**: `MainWindow._sort_sources_by_priority()` が `TLE_SOURCES["priority"]` 昇順でソート。`amateur`（汎用）を先にフェッチし、`cubesat`/`weather` 等がその後に上書きするよう保証

### tle_group と UI フィルタの対応

| tle_group 値 | UI フィルタ | 用途 |
|---|---|---|
| `amateur` | Amateur | アマチュア衛星全般（SATNOGS 登録衛星のデフォルト） |
| `cubesat` | CubeSat | CelesTrak CUBESAT グループ由来 |
| `weather` | Weather | 気象衛星 |
| `earth-obs` | Earth Observation | 地球観測衛星 |
| `science` | Science | 科学衛星 |
| `stations` | Space Stations | ISS・CSS 等 |
| `legacy` | Amateur | NORAD < 10000 の古い衛星（COALESCE で Amateur 扱い） |
| `NULL` | Amateur | TLE なし衛星（`COALESCE(tle_group, 'amateur')` でデフォルト適用） |

### TLE なし衛星の自動非表示ルール

`fetch_provisional_tles()` および `fetch_active_tles()` の Phase 2 で適用：

```
TLE が取得できなかった場合:
  status = 'unknown' or 'dead'  → 即時 is_hidden=2
  status = 'alive'
    tle_no_result_since が NULL  → 今日の日付を記録（猶予開始）
    30日以内                     → 紫イタリックで表示継続
    30日超過                     → is_hidden=2（自動非表示）

TLE が取得できた場合:
    tle_no_result_since を NULL にリセット（紫解除）
```

### fetch_active_tles() の2フェーズ設計

CelesTrak `GROUP=active`（全15,000機）は 403 Forbidden で取得不可のため、代替の2フェーズ構成を採用：

**Phase 1 — CelesTrak 複数グループ一括取得（高速）**
アクセス可能なグループを順に取得し、DB にある衛星のみ保存：
- `satnogs`（664機）・`last-30-days`（265機）・`argos`・`orbcomm`・`spire`
- 約 470機分のマッチ → INSERT（`tle_group='amateur'`）または UPDATE（`tle_group` 保持）
- 新規衛星レコードは作成しない

**Phase 2 — SATNOGS TLE API 並列フォールバック（最大 20 並列）**
Phase 1 後も TLE なしの `10000-89999` 衛星を個別照会：
- `GET https://db.satnogs.org/api/tle/?norad_cat_id={norad}&format=json`
- TLE あり → 保存（`source='satnogs'`, `tle_group='amateur'`）
- TLE なし → 上記の自動非表示ルールを適用

### 起動時の TLE 同期フロー

```
アプリ起動
  │
  ├─ APScheduler 開始（2h/4h/6h/12h/24h の定期ジョブを登録）
  │
  ├─ [バックグラウンド] _refresh_satellite_names_sync()
  │     1. sync_satellite_names()    ← SATNOGS 衛星名・ステータス更新・移行パイプライン
  │     2. fetch_provisional_tles()  ← NORAD ≥ 90000 衛星の TLE 取得
  │     3. fetch_legacy_tles()       ← NORAD < 10000 衛星のクリーンアップ（初回のみ実質動作）
  │
  └─ [バックグラウンド・stale時のみ] _refresh_active_tle_sync()
        fetch_active_tles()          ← NORAD 10000-89999 未収録衛星の TLE 補完（24h 経過時）
```

### DB マイグレーション注意事項（2026-05-29 バグ対応済み）

`tle_data` テーブルの CHECK 制約変更時はテーブル再作成が必要（SQLite 制約）。
過去に `SELECT *` による列順序不一致でデータロスが発生した。

**現在の正しい実装**（`database.py _apply_migrations()`）：
- 列名を明示した `INSERT OR IGNORE INTO tle_data (col1, col2, ...) SELECT col1, col2, ...`
- `_tle_data_backup` テーブルが残存していれば（前回のマイグレーション中断の証拠）自動復旧
- `SELECT *` は絶対に使用しないこと
