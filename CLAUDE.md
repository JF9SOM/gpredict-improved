# CLAUDE.md — GPredict-Improved 開発指示書

このファイルはClaude Codeが本プロジェクトを理解し、一貫した判断をするための指示書です。
コードを書く前に必ずこのファイルを参照してください。

---

## 最重要ルール：実装前に必ずユーザーの了承を得ること

**いかなるコード変更・実装も、ユーザーが明示的に承認してから行うこと。**

- 「どうすればいいか」「何を直せばいいか」がわかっても、勝手に実装しない
- 実装方針を提案し、ユーザーが「OK」「やってください」等の承認を与えてから実装する
- ユーザーが依頼した内容のみを実装する。関連して気になる箇所があっても、了承なく追加・修正しない
- バグを発見しても、依頼されていない修正は勝手に行わない

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
│   ├── sdr/            # SoapySDR バックエンド（デバイス・パイプライン・復調・録音）
│   ├── comms/          # デジタル通信（APRS・テレメトリー等）
│   │   └── aprs/       # APRSEngine・Direwolf管理・Bell 202 AFSK復調・AX.25パーサー
│   ├── data/           # データ同期（SATNOGS・TLE）・SQLiteDB・手動編集・テレメトリーフォーマット定義
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

### 自動フェッチスケジュール（APScheduler）

アプリはバックグラウンドでTLE・トランスポンダーを自動更新する。**手動更新は通常不要。**
ユーザーは **Help → Auto Fetch Rules** でこのスケジュールを確認できる。

| データ種別 | 更新間隔 | APSchedulerジョブ |
|---|---|---|
| Space Stations（ISS・CSS等） | 1時間ごと | `_refresh_tle_sync`（各ソースの`update_interval_hours`を参照） |
| Amateur Satellites | 2時間ごと | 同上 |
| CubeSats | 4時間ごと | 同上 |
| Weather Satellites | 6時間ごと | 同上 |
| Earth Observation / Science | 12時間ごと | 同上 |
| Provisional TLEs（NORAD ≥ 90000） | 12時間ごと | `provisional_tle_refresh` |
| Active TLE fallback（NORAD 10000–89999） | 24時間ごと | `active_tle_refresh` |
| AMSAT運用状況 | 24時間ごと | `amsat_refresh` |

SATNOGSトランスポンダーは**初回起動時に自動取得**。以降は `Satellite → Sync SATNOGS` で手動更新。

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

### Hamlib バージョン管理・配布方針（2026-06-09 確定）

#### 必須バージョン
- **Hamlib 4.7.1 以上が必須**（FTX-1F モデル 1051 および SkyWatcher ローテーターは 4.7 以降でのみ動作）
- 配布バンドル（AppImage / .exe / .dmg）には必ず 4.7.1 を同梱すること

#### バンドル版 Hamlib のビルド

| プラットフォーム | ビルド方法 | PyInstaller 収集元 |
|---|---|---|
| Linux | ソースから `/opt/hamlib/4.7` にビルド | `/opt/hamlib/4.7/lib/*.so` |
| Windows | 公式 `hamlib-w32-4.7.1.zip` を展開 | `hamlib-win64\bin\*.dll` + Python bindings |
| macOS | Homebrew `brew install hamlib` | `$(brew --prefix hamlib)/lib/` |

#### in-app Hamlib アップデーター（Help > Hamlib Update…）

ユーザーが GUI からバンドル版を上書きできる仕組み。AppImage・exe・dmg は読み取り専用なのでバンドルは変更できず、代わりにユーザーデータディレクトリへインストールする。

**インストール先:**
```
Linux:   ~/.local/share/gpredict-improved/hamlib/
macOS:   ~/Library/Application Support/gpredict-improved/hamlib/
Windows: %APPDATA%/gpredict-improved/hamlib/
```

**起動時のロード優先順位:**
1. ユーザーインストール版（`sys.path.insert(0, ...)` で先頭に追加）
2. バンドル版 / システム版

**Windowsの追加処理**: `os.add_dll_directory(user_hamlib_dir)` が必要（Python 3.8+）。`main.py` の起動ブロックで実施済み。

**GitHub Releases アセット命名規則:**（CI が自動アップロード）

| プラットフォーム | ファイル名 | 内容 |
|---|---|---|
| Linux | `hamlib-linux-x86_64-py311-4.7.1.tar.gz` | `$ORIGIN` rpath付きポータブルビルド |
| Windows | `hamlib-windows-x86_64-py311-4.7.1.zip` | フラットレイアウト（DLL + .pyd + Hamlib.py） |
| macOS | `hamlib-macos-arm64-py311-4.7.1.tar.gz` | `@loader_path` rpath + dylibbundler で依存解決済み |

`py311` の部分は Python バージョンに応じて変化（`hamlib_info.py` の `_PYVER_TAG` で決定）。

**関連ソースファイル:**
- `src/core/hamlib_info.py` — バージョン検出・ユーザーディレクトリ・アセット命名
- `src/ui/hamlib_update_dialog.py` — ダウンロード・展開・インストール UI
- `src/main.py` — ユーザーインストール版の優先ロード・Windows DLL パス登録
- `.github/workflows/ci.yml` — 各プラットフォームのポータブルパッケージビルドと Release アップロード

#### Linux 開発環境固有: sys.path surgery

開発機（`/opt/hamlib/4.7` が存在する場合のみ）は `/usr/lib/python3/dist-packages` を `sys.path` から除去して 4.7.1 を優先ロードする。

**重要**: このブロックは `os.path.exists(_HAMLIB_SITE)` でガードされており、`/opt/hamlib/4.7` が存在しない一般ユーザー環境では一切実行されない。

SoapySDR も同じ `dist-packages` に存在するため、パス除去前に `import SoapySDR` をプリロードして `sys.modules` に保持する（`main.py` の `contextlib.suppress` ブロック）。

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

### mypy とオプショナルインポートの注意点（2026-06-12 確定）

CI は `pip install -e ".[dev]"` のみ実行するため、`scipy` などのオプショナル依存は**インストールされない**。
`pyproject.toml` の `ignore_missing_imports = true` により、mypy はインストールされていないモジュールのインポートを `Any` として扱い、エラーを出さない。

**オプショナルインポートの正しいパターン（`type: ignore` コメント不要）:**

```python
try:
    from scipy import signal as sp_signal
    _SCIPY_AVAILABLE: bool = True
except ImportError:
    sp_signal = None   # type: ignore コメント不要
    _SCIPY_AVAILABLE = False
```

**やってはいけないパターン:**

```python
# NG1: 前方宣言すると import 自体が no-redef エラーになる
sp_signal: Any
try:
    from scipy import signal as sp_signal  # error: no-redef
    ...

# NG2: type: ignore[assignment] / [no-redef] / [unused-ignore] を付けると
#      CIでは「Unused type: ignore comment」として弾かれる
except ImportError:
    sp_signal = None  # type: ignore[no-redef]  ← CI で unused-ignore エラー
```

**理由:** mypy は `try/except ImportError` の except ブランチを「import が失敗した場合の新規定義」と解釈するため、`no-redef` も `assignment` もエラーにならない。`ignore_missing_imports = true` 環境ではさらにすべてが `Any` 扱いとなり、あらゆる `type: ignore` コメントが「未使用」として弾かれる。

---

## ビルド・配布

- **Linux**: AppImage（全distro対応）+ `.deb`（Ubuntu/Debian）
- **Windows**: PyInstaller → NSIS インストーラー `.exe`
- **macOS**: PyInstaller → `.dmg`
- **GitHub Actions**: タグpushで3プラットフォーム自動ビルド → GitHub Releases

---

## CI/CD トラブルシューティング履歴（v0.1.0-beta.34 で解決済み）

v0.1.0-beta.34 の CI 作業で判明した重要な知見。同様のエラーに遭遇したときのために記録する。

### Hamlib 4.7.1 ソースビルド共通

**問題**: `hamlib_wrap.c: No such file or directory`  
**原因**: Hamlib 4.7.1 ソースtarballには SWIG が生成する `hamlib_wrap.c` が含まれない（`.swg` ファイルのみ）  
**解決**: ビルド前に `swig -python -Iinclude -Ihamlib-4.7.1/include -o bindings/hamlib_wrap.c bindings/hamlib.i` を実行

**問題**: `hamlib/config.h: No such file or directory`  
**原因**: `config.h` は autotools が生成するファイル。tarball・zip には含まれない  
**解決**: 必要な define のみ含む最小スタブを手動作成してインクルードパスに配置

### macOS 固有

**問題**: `symbol(s) not found for architecture arm64`（Python シンボルリンクエラー）  
**解決**: `clang` コンパイル行に `-undefined dynamic_lookup` を追加（macOS では Python シンボルを明示的にリンクしない）

**問題**: dylibbundler が `/tmp` シンボリックリンクで自己削除エラー  
**原因**: macOS の `/tmp` は `/private/tmp` へのシンボリックリンク。コピー元とコピー先が同一パスに解決される  
**解決**: prefix を `/tmp/` ではなく `$HOME/hamlib-portable-mac/` に変更

**問題**: dylibbundler が `--dest-dir` と同じ場所を参照して無限ループ  
**解決**: `--dest-dir ${PORTABLE_LIB}/deps` + `--install-path @loader_path/../deps` に変更

### Windows 固有

**問題**: `ImportError: DLL load failed while importing _Hamlib`（ABI ミスマッチ）  
**原因**: MSVC でコンパイルした `.pyd` と MinGW でビルドした `libhamlib-4.dll` は ABI が合わない  
**解決**: Python binding のコンパイルも MinGW GCC に統一。`hamlib-w32-4.7.1.zip`（32bit）ではなく `hamlib-w64-4.7.1.zip`（64bit）を使用

**問題**: Python 3.8+ で PATH 経由の DLL 探索が効かない  
**解決**: `os.add_dll_directory()` を使用（`main.py` 起動ブロックに実装済み）

**問題**: PyInstaller が作成した `dist\gpredict-improved\` が空になる  
**原因**: Windows Defender のリアルタイムスキャンが新規作成された未署名 exe/DLL を検疫  
**解決**: PyInstaller 実行前に `Set-MpPreference -DisableRealtimeMonitoring $true` を追加  
**追加対策**: `choco install nsis` は必ず PyInstaller より前のステップで実行する（PyInstaller 後に実行すると dist が消える可能性）

**問題**: `File: "dist\gpredict-improved\" -> no files found.`（NSIS）  
**原因**: NSIS の `File` コマンドは相対パスを **スクリプトファイルの場所**（`scripts\`）基準で解決する。CWD 基準ではない。`File /r "dist\gpredict-improved\"` は `scripts\dist\gpredict-improved\` を探しに行く  
**解決**: `File /r "..\dist\gpredict-improved\"` に変更（`scripts\` の一つ上 = リポジトリルート）

**問題**: `Can't open output file` / `Output: scripts\dist\GPredict-Improved-Setup.exe`（NSIS）  
**原因**: `OutFile` も同様にスクリプトファイル基準で解決される  
**解決**: `OutFile "..\dist\GPredict-Improved-Setup.exe"` に変更

> **NSIS パス解決の原則**（重要）:
> `scripts\installer.nsi` 内のすべてのファイル系ディレクティブ（`File`・`OutFile`・`Icon` 等）は、**スクリプトファイルが置かれているディレクトリ**（`scripts\`）を基準に相対パスを解決する。リポジトリルートの `dist\` を参照するには必ず `"..\dist\..."` と書くこと。  
> なお `makensis` コマンドライン引数（`/DAPP_VERSION` 等）や PowerShell 側の変数は CWD 基準で問題ない。

**問題**: `Error: invalid VIProductVersion format, should be X.X.X.X`（NSIS）  
**原因**: `VIProductVersion` は Windows リソースの仕様で `X.X.X.X`（数値4フィールド）必須。`0.1.0-beta.34` は不正  
**解決**: CI で `-beta.34` を除去して `.0` をパディングした `VIVERSION=0.1.0.0` を別途計算し、`/DVIVERSION=$viVer` で渡す。表示用の `APP_VERSION` は semver のまま維持

```powershell
$numericVer = ($ver -replace '-.*$', '')
$parts = $numericVer.Split('.')
while ($parts.Count -lt 4) { $parts += '0' }
$viVer = ($parts[0..3] -join '.')
makensis /DAPP_VERSION=$ver /DVIVERSION=$viVer scripts\installer.nsi
```

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

## 実装済み機能一覧（2026年6月13日時点・v0.1.0 + feature/communications）

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
- **Autotrack / Record メニュー**（メニューバー。Radio と View の間）
  - クリックで非モーダルダイアログ `AutotrackRecordDialog`（src/ui/autotrack_record_dialog.py）を開く
  - **Autotrack Lists 枠**（最上部）: リスト作成・衛星＋トランスポンダー登録・並び替え（Settings から移動）
  - **Autotrack Control 枠**: リスト選択コンボ・Enable チェックボックス・ステータス表示・?ヘルプボタン
  - **Recording 枠**: Audio Record (MP3) / IQ Record チェックボックス（AOS で自動開始・LOS で自動停止）
  - **Autotrack Timer 枠**: 開始時刻（カレンダーポップアップ付き QDateTimeEdit + Now ボタン）・停止時間（3/6/12/24時間コンボ）
    - View > Time Zone 設定に連動: UTC モードなら「Start (UTC):」、Local モードなら「Start (Local):」表示
    - 指定時刻になると Autotrack を自動開始、停止時刻になると自動停止（リグ・ローテーター切断・録音停止）
  - Radio Control タブの Autotrack 枠は「ON/OFF」のコンパクトインジケーターのみに縮小
  - AOS 時に自動でリグ＋ローテーターを接続、LOS 時に自動切断
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
- **GitHub Actions: `make_latest: true`**（`prerelease: true` を廃止）。3プラットフォーム全ビルドジョブで設定済み。最新リリース: `v0.1.0`
- **Open in SatNOGS（クロスプラットフォーム）**: 右クリックメニューから衛星の SatNOGS ページをアプリモードで開く。`_open_url_app_mode` に統一済み（Linux: `shutil.which` / macOS: `.app` 絶対パス / Windows: `Program Files` 絶対パス）。Chromium系が見つからない場合は `QDesktopServices.openUrl` にフォールバック
- **メニューバー構成**（v0.2.0 以降）
  - File / Satellite / Radio / **Communications** / **Autotrack/Record** / View / Help
  - **Communications**: サブメニュー APRS / Telemetry（クリックで非常駐タブを開く。× で閉じる）
  - **Autotrack/Record**: サブメニューなし。クリックで AutotrackRecordDialog を開く
  - **View メニュー**: Language（English 動作 / Japanese は「To be prepared later.」）・Time Zone（UTC / Local Time）
  - Radar・Pass Chart エントリは削除済み（タブ直接選択で十分。Dashboard追加によるインデックスずれ問題を根本解決）
- **フッター RIG ラベル**（`_update_rig_label`）: Hamlib リグだけでなく SDR（SdrRigAdapter）接続時も「RIG: 1」「RIG: 2」「RIG: 1+2」に更新。`RadioControlWidget` に `rig_disconnected` / `rig2_disconnected` シグナルを追加し、切断時も「RIG: Off」に戻るよう修正済み
- CI緑（mypy strict + pytest）

### SDR 機能（v0.1.0 時点で実装済み）

- **SoapySDR バックエンド**（`src/sdr/`）: device・pipeline・demodulator・recorder
  - SdrDevice: SoapySDR デバイス列挙（audio/null/remote ドライバ除外）・オープン・ストリーミング
  - SDRPipeline（QThread）: I/Q 取得 → FFT（10fps スペクトラム）→ 復調 → 音声出力 → IQ 録音
  - Demodulator: NFM / USB / LSB / CW 各モード。DC ブロック IIR（30Hz HPF）で HackRF DC スパイク除去
  - CW 復調: エンベロープ検出なし・直接復調方式（ブーン音問題を根本解決）
- **SdrRigAdapter**（`src/rig/controller.py`）: RigController を継承し SDR を Rig として扱う
  - `is_sdr = True` プロパティで UI 側が SDR スロットを識別
  - connect() で sample_rate / ppm / gain / bias_tee を一括適用
- **Rig Settings > SDR Settings タブ**（第3タブ）
  - デバイス列挙・選択、サンプルレート、PPM補正、RFゲイン（Auto/Manual）
  - **Bias-T ON/OFF チェックボックス**（ドライバ別キー自動選択: HackRF=`bias_tx`/`"true"`, RTL-SDR=`biastee`/`"1"`）
  - Rig 1 / Rig 2 割り当てラジオボタン（割り当てたスロットの Hamlib タブを自動グレーアウト）
  - Hamlib バージョン表示は Rig 1/2 タブのみ（SDR タブには非表示）
- **SDR Control タブ**（常時表示・SDR未接続時はパネルをグレーアウト）
  - スペクトラムアナライザ（QtCharts、10fps）＋ **RX 周波数リアルタイム表示**（`center_freq_changed` Signal）
  - **Passband Tune パネル**: ◀◀/◀/▶/▶▶ ボタン + ステップ選択（100Hz〜10kHz）+ オフセット表示 + Reset
    - SDR が Rig 1/Rig 2 どちらでも動作
    - Lock ON 時: 相手リグの TX を自動追従（反転トランスポンダーは符号反転）
    - トランスポンダー切り替え時にオフセット自動リセット
  - デモジュレーター（モード選択・ボリューム・AGC・Start/Stop Audio）
    - **MP3音声録音**（`● REC Audio` / `■ STOP` / `📁`）— `lameenc` によるピュアPythonエンコード、外部ツール不要
  - IQ レコーダー（帯域幅選択・REC/STOP・経過時間表示）
    - **📁ファイルマネージャーボタン**（IQ・Audio 両方）— SDR未接続時も常時クリック可能。巨大IQファイルの削除に使用
  - トランスポンダー選択に連動したモード自動切替（Connect 前でも反映）
- **Help > Hamlib Update…**（in-app Hamlib アップデーター）
  - GitHub Releases から最新 hamlib バンドルをダウンロード・展開・ユーザーディレクトリへインストール
  - Linux / Windows / macOS 対応
- **Help > Check for Updates…**（アプリ自動更新）
  - GitHub Releases API で最新バージョンを確認
  - Windows: インストーラー（.exe）をダウンロードしてサイレントインストール
  - Linux: AppImage を置き換え
  - macOS: dmg をマウントして .app をコピー
- **Windows NSIS インストーラー形式**（ZIP 配布から変更）
  - `scripts/installer.nsi`: スタートメニュー・デスクトップショートカット・Add/Remove Programs 登録
  - サイレントインストール（`/S` フラグ）対応
- **実動作確認済みリグ・デバイス**（2026-06-13）
  - FTX-1F（Hamlib 4.7.1 モデル1051、NET Control）: ドップラー補正・VFO制御・CTCSS 動作確認済み
  - FTX-1F（Hamlib 4.7.1 モデル1051、Direct モード）: モード・CTCSS（raw CAT `MD1/MD0/CN1/CT1` via `os.open()`）動作確認済み（2026-06-18）
  - FT-991AM（Hamlib 4.7.1 モデル1036、NET Control）: ドップラー補正・VFO制御・CTCSS 動作確認済み
  - FT-991/FT-991A（Direct モード）: モード・CTCSS（raw CAT `SV/MD0/CN0/CT0` via pyserial）実装済み・実機確認待ち（2026-06-18）
  - HackRF One（SoapyHackRF）: NFM/USB/CW 復調・スペクトラム・Bias-T 動作確認済み（Linux/Windows）
  - RTL-SDR（SoapyRTLSDR）: 基本動作確認済み（Linux/Windows）
  - Airspy R2・Mini（SoapyAirspy）: Windows バンドル同梱・Linux brew/apt 対応（実機未確認）
  - Airspy HF+（SoapyAirspyHF）: Windows バンドル同梱・Linux brew/apt 対応（実機未確認）
  - ADALM-Pluto（SoapyPlutoSDR + libiio）: Windows バンドル同梱（CI にて MSVC ソースビルド）・実機未確認
  - Rig 1（FTX-1F）+ Rig 2（RTL-SDR）デュアル構成: Passband Tune + Lock 連動動作確認済み

### Communications 機能（feature/communications ブランチ・v0.2.0 実装済み）

**ディレクトリ構成:**
```
src/
├── comms/
│   ├── aprs/
│   │   ├── engine.py       # APRSEngine — Direwolf/SDR 両パス統合・PTT制御
│   │   ├── parser.py       # AX.25 フレームデコード・APRS パース
│   │   ├── afsk_demod.py   # Bell 202 AFSK 1200 baud デモジュレーター（SDR 受信パス）
│   │   └── direwolf.py     # Direwolf サブプロセス管理・KISS TCP クライアント
│   └── telemetry/
│       └── decoder.py      # テレメトリーフレームデコーダー（JSON 定義ベース）
├── data/
│   └── telemetry_formats/  # 衛星ごとのバイナリテレメトリーフォーマット定義（JSON）
```

**メニュー: Communications > APRS**（`src/ui/aprs_tab.py`）
- 受信ログ（タイムスタンプ / コールサイン / Via / 内容）
- 入力ソース自動切替: SDR → Bell 202 AFSK 受信専用 / Rig+サウンドカード → Direwolf TX/RX
- **メッセージ送信**: To / Message フォーム + Send ボタン（Rig+Direwolf 接続時のみ有効）
- **自局位置送信**（"Send My Position" グループ）:
  - `Auto-beacon every N min` チェックボックス（1〜60分間隔、ON時即時送信）
  - シンボル選択（Fixed Station `/-` / Mobile `/>` / Balloon `/O` / Antenna `/Y` / Satellite `/S`）
  - Comment テキスト（最大43文字）
  - Send Now ボタン
  - QTH座標を `LocationManager.load_saved()` から自動取得・表示
- **APRS位置パケット → Dashboardマップピン表示**（シアン▲マーカー + コールサインラベル）
  - `aprs_stations_updated(dict)` シグナル → `WorldMapView.set_aprs_stations()`
  - タブクローズ時 `aprs_stations_cleared()` → `WorldMapView.clear_aprs_stations()`
- ADIF エクスポート（.adi ファイル）
- SQLite `aprs_log` テーブルへ自動永続化

**メニュー: Communications > Telemetry**（`src/ui/telemetry_tab.py`）
- AX.25 フレーム受信 → JSON フォーマット定義でフィールドデコード
- 定義なし衛星は生 hex + 衛星名表示
- CSV エクスポート
- SQLite `telemetry_log` テーブルへ自動永続化

**テレメトリーフォーマット定義**（`src/data/telemetry_formats/`）
| NORAD | 衛星名 | コールサイン | フィールド |
|-------|--------|-------------|-----------|
| 25544 | ISS (ARISS) | RS0ISS | なし（APRS パケット識別用） |
| 40908 | LilacSat-2 | BJ1SK | EPS 7項目 ※未検証 |
| 42017 | Nayif-1 (EO-88) | A6-NAYIF | EPS 5項目 ※未検証 |
| 42829 | Uguisu (BIRDS-1) | JG6YBW | EPS 4項目 ※未検証 |
| 42830 | GhanaSat-1 (BIRDS-1) | GSAT-1 | なし（名前識別用） |
| 43786 | ITASAT-1 | PY2ITA | EPS 4項目 ※未検証 |
| 43803 | JY1Sat (JO-97) | JY1SAT | なし（フォーマット非公開） |
| 43931 | Nexus (FO-99) | JG6YIG | EPS 6項目 ※未検証 |
| 44829 | DHABISAT (MYSat-2) | A6-DBSAT | なし（名前識別用） |
| 47311 | Maya-2 (BIRDS-2) | DU3ABE | EPS 4項目 ※未検証 |
| 47783 | GOLF-TEE (AO-109) | WJ9H | EPS 5項目 ※未検証 |

※ Fox-1シリーズ（AO-85/91/92）は DUV 200 baud のため 1200 baud AFSK デモジュレーターでは受信不可。

**PTT CAT 制御**（`src/rig/controller.py`）
- `RigController.set_ptt(enabled: bool)`: 基底クラスで `_ptt_active` フラグを管理
- `HamlibNetController.set_ptt()`: rigctld `T 1` / `T 0` コマンド
- `HamlibDirectController.set_ptt()`: Hamlib binding `rig.set_ptt()`
- **Doppler 凍結**: TX 中（`_ptt_active=True`）は `set_vfo_frequencies()` が早期リターン → 送信中の周波数変更を防止（約0.8秒: lead 150ms + audio 550ms + tail 100ms）

**PTT 送信シーケンス**（APRSメッセージ・位置パケット共通）:
```
PTT ON (CAT) → 150ms 待機 → KISS フレーム送信 → 550ms 待機 → 100ms 待機 → PTT OFF
```
全シーケンスは daemon スレッドで実行（Qt UI スレッドをブロックしない）。

**Help > Direwolf Installation…**（`src/ui/direwolf_dialog.py`）
- 現在使用中の Direwolf パス・バージョン・ソース（User-installed / System PATH / Bundled）を表示
- プラットフォーム別インストール案内（Linux: `apt install` コマンドコピー / Windows: GitHub Releases リンク / macOS: `brew install`）
- 「Download & Install」ボタン: GitHub Releases からバンドル版を取得・ユーザーディレクトリへインストール

**Bell 202 AFSK デモジュレーター**（`src/comms/aprs/afsk_demod.py`）
- SDR パスで AX.25 フレームを 1200 baud AFSK で受信
- アルゴリズム: デシメーション → 瞬時位相差分 → ボックスフィルター → NRZI デコード → HDLC 同期 + CRC-16/CCITT
- scipy 利用可能な場合は FIR フィルター付きデシメーション、不可の場合はストライドで代替
- `frame_received(bytes)` Signal で `KissClient` と互換インターフェース

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
- fill: `rgba(100,200,255,140)`、outline: シアン `#00DCFF` 1.5px

**アウトラインスキップ規則（重要）**:
- `is_full_width[i] or is_full_width[i+1]` でスキップ（どちらか一方でも全幅行なら除外）
- `xl=0` / `xr=w` という人工座標が通常行の実座標と結ばれて横線になるのを防ぐ
- `and` 条件（両端とも全幅行のみスキップ）は横線を発生させるため使用禁止
- 水平幅 `abs(x2 - x1) < w/3` のセグメントのみ描画（日付変更線越えの大ジャンプを除外）
- スキップにより極境界の弧は閉じない（開いて見える）が、1.5px の細線で目立たなくする妥協策を採用（beta.32）
- 極境界を完全に閉じる根本修正は未解決。遷移点の通常行座標で水平閉じ線を引く方式を試みたが、遷移行の xl≈0/xr≈w により閉じ線自体も横線になる副作用があり断念

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
1. **Autotrack/Record メニュー** → Autotrack Lists 枠でリスト作成・衛星登録
2. Upcoming Passes > Group タブでパス検索実施
3. Autotrack/Record ダイアログで Autotrack Control 枠のリストを選択 → Enable Autotrack をオン
4. （任意）Autotrack Timer で開始・停止時刻を設定
5. （任意）Recording 枠で Audio / IQ 録音を有効化

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

## 次回の作業候補（v0.1.0 以降）

### 継続中・優先度高
1. **ドップラー補正の実動作確認** — 各種リグ（IC-9700・TS-2000・FT-817ND 等）での実衛星通信テスト（FTX-1F・FT-991AM・RTL-SDR/HackRF は確認済み）
2. **ローテーター設定ダイアログの改善** — 接続テストボタン・AZ/ELリミット設定
3. **デバッグ用ログファイル出力の削除または設定化** — `src/main.py` の `_setup_logging()` にある frozen バンドル向けファイルログ出力（`platformdirs.user_log_dir`）は dmg デバッグ目的で追加したもの。Settings に「デバッグログを保存する」チェック（デフォルトOFF）を追加するか削除する。該当箇所: `src/main.py` 63〜75行目
4. ~~**Autotrack/Record メニューの実装**~~ **→ v0.1.0 以降で完了**（AutotrackRecordDialog・Autotrack Timer・AOS/LOS 自動接続・録音自動制御）

### モバイル・Web UI
5. **スマホ・タブレット画面の継続確認** — Android 実機でのコンパス連動確認、各種ブラウザでの表示確認

### SDR・デジタルモード
6. ~~**SDR機能の追加（フェーズ1: 初期実装）**~~ **→ v0.1.0 で完了**
7. ~~**APRS 受信・送信・位置ビーコン実装**~~ **→ feature/communications（v0.2.0）で完了**（APRSEngine・Direwolf統合・Bell 202 AFSK復調・PTT CAT制御・Doppler凍結・地図ピン表示）
8. ~~**Telemetry タブ実装**~~ **→ feature/communications（v0.2.0）で完了**（AX.25受信・JSON定義デコード・12衛星フォーマット定義）
9. **テレメトリーフォーマット定義の追加・検証** — 実際に受信したパケットでオフセット・スケールの検証。未定義衛星のフォーマット調査
10. **CI: Direwolf バンドルビルド** — GitHub Actions で Linux/Windows/macOS 向けバンドル版 Direwolf を自動ビルドし Releases にアップロード（タグ push 時）
11. **SDR フェーズ2（将来）— アマチュア衛星・デジタルモード** — HRPT/LRPT 画像・FT4・gr-satellites・AI-CW・SSTV（設計方針は「SDR 機能設計方針」セクション参照）
12. **SDR フェーズ2（将来）— 業務用衛星受信** — Inmarsat-C (STD-C)・Cospas-Sarsat L帯・Iridium L帯 ACARS・Orbcomm・みちびき（QZSS）データ放送（詳細は「業務用衛星受信」セクション参照）
13. **SDR Device Installation ダイアログ**（Help メニュー）— USB VID/PID スキャン・ドライバ自動インストール誘導
14. **Help > gr-satellites… ダイアログ** — システムインストール検出・案内（GNU Radio が必須のため自動インストールは行わない）

### 配布・ビルド
15. **Windows・macOS v0.1.0 ビルドの動作確認** — CI ビルド成功後、実機での SDR 含む全機能検証

### データ・連携
16. **観測ログ機能** — 実際に追尾・通信した衛星パスを記録・集計・エクスポートする機能
17. **多言語対応（日本語）** — フェーズ2として日本語UIの追加（翻訳ファイルは準備済み。View > Language > Japanese は現在「To be prepared later.」表示）

### ハードウェア連携
18. **追加リグの実機テスト** — IC-9700・TS-2000・FT-817ND 等でのドップラー制御動作確認（satmode含む）
19. **WSJT-X / JS8Call 連携** — デジタルモード運用ソフトとの周波数・モード連動（将来）

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

**クロスバンド（satmode リグ）の正しい順序:**
```
S 1 Main  （satmode確立）
V Sub  → M {ul_mode} 0  （Sub=TX=アップリンク）
V Main → M {dl_mode} 0  （Main=RX=ダウンリンク）
```

**同バンド（satmode リグ・V/V または U/U）の正しい順序:**
```
S 1 VFOB  （通常split確立: VFOA=RX, VFOB=TX）
V VFOB → M {ul_mode} 0  （VFOB=TX=アップリンク）
V VFOA → M {dl_mode} 0  （VFOA=RX=ダウンリンク）（または V Main 相当）
```

**非satmode リグ（FTX-1F, FT-991A等）:**
```
V Sub  → M {ul_mode} 0  （Sub=TX=アップリンク）
V Main → M {dl_mode} 0  （Main=RX=ダウンリンク）
```

`send_mode_only()` は `_is_same_band` フラグで上記3パターンを自動分岐する。
S 1 Main / S 1 VFOB は `apply_transponder_state()` 内の `_send_split_init_independent()` でモード設定より先に独立ソケットで送信し、satmodeを確立してからmode・CTCSSを設定する（Direct modeと同じ順序）。`connect()` の `_init_vfo()` でも再送されるが、リグはすでにSATモードに入っているため冪等。

### 動作確認環境
- リグ: Yaesu FTX-1F
- PC: GPD MicroPC2 (Ubuntu)
- Hamlib: 4.7.1-rc (2026-02-16) モデルID 1051
- 接続: USB → /dev/FTX1CAT → udev/systemd → rigctld:4532

---

## Rig-Specific Implementation Notes

### FTX-1F (Hamlib model 1051)

#### NET モード
- rigctld backend forces Sub=TX, Main=RX regardless of S command argument (FTX-1F specific quirk; other rigs achieve the same result through standard split or satmode mechanisms)
- `S 1 Main` is required for split (not `S 1 Sub`) — rigctld standard protocol, universal across all rigs
- `F {hz}` → Main (RX/DL),  `I {hz}` → Sub (TX/UL) via rigctld — universal VFO assignment
- Mode setting: `V Sub → M {ul_mode} 0 → V Main → M {dl_mode} 0` via independent socket
- `V` (active VFO switch) command causes TX LED to light → forbidden in Doppler cycle
- CTCSS: Hamlib `L CTCSS_TONE` → `RPRT -11` (not supported by backend)
  Custom CAT via rigctld `w` command: `CN10{tone:03d};CT11;` / `CT10;`
  `CN P1=1:SUB, P2=0:CTCSS, P3=tone index 000-049`

#### Direct モード（`_FTX1_MODEL_IDS = frozenset({1051})`）
- ボーレート誤設定時に Hamlib がシリアル応答待ちでタイムアウトし（最大数十秒）、Python GIL を保持したまま UI がフリーズする問題を回避するため、モード・CTCSS 設定を Hamlib 経由で行わない（ボーレートが正しければ Hamlib でも動作するが、raw CAT の方が `set_vfo(VFOB)` を呼ばない分シンプル）
- トランスポンダー選択時に `_apply_mode_and_ctcss_cat_ftx1(dl_mode, ul_mode, ctcss_hz)` をバックグラウンドスレッドで呼び出す
- FTX-1F の `MD` コマンドは P1 で VFO を直接指定できる（P1=1=SUB, P1=0=MAIN）。SV スワップ不要
  ```
  MD1{ul_code};     — SUB (TX/UL) モード設定
  MD0{dl_code};     — MAIN (RX/DL) モード設定
  CN10{tone:03d};   — CTCSSトーン番号（P1=1:SUB, P2=0:CTCSS）
  CT11;             — CTCSS ENC ON（SUB=TX 側）
  CT10;             — CTCSS OFF
  ```
- 書き込みは `os.open(O_WRONLY|O_NOCTTY|O_NONBLOCK)` で行う（ポートが Hamlib に占有されていなければ動作）
- **注意**: `os.open()` は termios を設定しない。Hamlib が事前にポートを開いてボーレートを設定している場合のみ正しく動作する。ユーザーがボーレートを正しく設定していることが前提（Rig Settings のボーレートテストボタンで確認可能）

### FT-991 / FT-991A (Hamlib models 1035 / 1036)

Hamlib 4.7.1 の公式モデルリスト: **1035 = FT-991**（FT-991A も同バックエンドを使用）。
rig_dialog.py のカスタムリストでは 1036 = FT-991A として登録。`_FT991_DIRECT_MODEL_IDS = frozenset({1035, 1036})` で両方を対象にする。

#### NET モード（`ctcss_method == "ft991"` で識別）
- `MD` コマンドは P1=0 固定（Main VFO のみ対象）。VFO-B（UL）のモード設定には SV スワップが必要
- Hamlib `set_mode(RIG_VFO_B)` → `-11 Feature not available`
- CTCSS: Hamlib `L CTCSS_TONE` → `RPRT -11` (not supported by backend)
  Custom CAT: `CN00{tone:03d};CT02;` / `CT00;`
  `CN P1=0:fixed, P2=0:CTCSS, P3=tone index 000-049`
  `CT P2=2`: CTCSS ENC only; `CT00;` to disable
- rigctld `w CN…` works but requires FM mode to be active on the rig
- `SV`/`MD` commands via rigctld `w` each take ~2 s (wait for RPRT with 2 s timeout)
- `send_mode_only()` の FT-991 パス（`ctcss_method == "ft991"`）:
  ```
  MD0{dl_code};                — VFO-A (DL) モード設定
  SV; MD0{ul_code}; SV;       — VFO-B (UL) モード設定（SV スワップ）
  ```
- `send_ctcss_cat()`: `CN00{tone:03d};CT02;` を SV スワップなしで送信
  → `CT02`（CTCSS ENC）は TX-VFO（スプリット時は VFO-B）にグローバルに適用されるため SV 不要（FT-991AM で動作確認済み）
- `send_mode_only()` はバックグラウンドスレッドで実行（UI フリーズ防止）

#### Direct モード（`_FT991_DIRECT_MODEL_IDS = frozenset({1035, 1036})`）
- トランスポンダー選択時に `_apply_mode_and_ctcss_cat_ft991(dl_mode, ul_mode, ctcss_hz)` をバックグラウンドスレッドで呼び出す
- FTX-1F と異なり MD P1 固定のため VFO-B モード設定は SV スワップが必要:
  ```
  SV;               — VFO-B を Main に切り替え
  MD0{ul_code};     — UL モード設定（現 Main = 元 VFO-B）
  SV;               — 元に戻す
  MD0{dl_code};     — DL モード設定（Main = VFO-A）
  CN00{tone:03d};   — CTCSS トーン番号（SV スワップ不要: TX-グローバル）
  CT02;             — CTCSS ENC ON
  CT00;             — CTCSS OFF
  ```
- 書き込みは **pyserial** を使用（`os.open()` と異なり termios / ボーレートを正しく設定）
- `_port_lock` を取得して `connect()` との競合を防ぐ
- `_FT991_MODE_MAP`（`HamlibNetController` と共用）を使用してモードコードを引く
- main_window.py では `_FTX1_MODEL_IDS | _FT991_DIRECT_MODEL_IDS` をまとめて同一ブランチで処理

### IC-9700 / IC-9100 / IC-910H / IC-821H (Icom satmode rigs — `_SATMODE_RIG_IDS`)
- These rigs implement Hamlib **satmode**: firmware always routes Main=RX(DL) and Sub=TX(UL)
- Direct mode split init: Hamlib `set_func(RIG_FUNC_SATMODE, 1)` — `open → set_func → close → open` sequence
- Direct mode freq (cross-band): `set_freq(RIG_VFO_MAIN, dl_hz)` + `set_freq(RIG_VFO_TX, ul_hz)`
- Direct mode mode + CTCSS: Hamlib `_apply_mode_and_ctcss_hamlib()` (before connect) or `_satmode_exit()` (same-band at connect time)
- `HamlibDirectController._satmode` flag is set automatically when model_id ∈ `_SATMODE_RIG_IDS`

#### Cross-band UL frequency write — VFO_TX approach (confirmed 2026-06-20)

**Hamlib `set_func(RIG_FUNC_SATMODE, 1)` works correctly** in Hamlib 4.7.1:
- IC-9100/IC-9700: sends CI-V `16 5A 01` (SAT mode ON)
- IC-910H: sends CI-V `1A 07 01` (different command, handled automatically by Hamlib model backend)
- IC-821H: same `16 5A` as IC-9100

**CI-V commands for reference**:
- `FE FE <civ_addr> E0 16 5A 01 FD` — SAT mode ON (IC-9100/9700/821H)
- `FE FE <civ_addr> E0 16 5A 00 FD` — SAT mode OFF
- `FE FE <civ_addr> E0 16 59 xx FD` — Dual Watch (completely unrelated; do NOT confuse with SAT mode)

**Python binding caveat**: `rig.set_func()` takes exactly **2 arguments** `(func, status)`. Calling with 3 arguments `rig.set_func(CURR, SATMODE, 1)` silently passes `func=CURR` (a VFO constant), which causes `rig_has_set_func` to return 0 → ENAVAIL → no CI-V is sent. Always use `rig.set_func(RIG_FUNC_SATMODE, 1)`.

**Implementation (src/rig/controller.py)**:
- `connect()`: for satmode rigs — `rig.open()` → `time.sleep(0.3)` → `rig.set_func(RIG_FUNC_SATMODE, 1)` → `rig.close()` → `rig.open()`. Second open reads satmode=1 from rig, sets `cache->satmode=1`, which allows `set_freq(VFO_TX)` for UL writes.
- `satmode_warmup()`: same open→set_func→close sequence, called from background thread at startup. Imports Hamlib directly (`import Hamlib as _H`) rather than using `self._hamlib`, because `self._hamlib` is `None` until the first `connect()` call.
- `_init_split()`: just sets `_satmode_active = True` — SAT mode was already entered by `connect()`
- DL: `set_freq(RIG_VFO_MAIN, dl_hz)` as before
- UL (periodic): `set_freq(RIG_VFO_TX, ul_hz)` — works because `cache.satmode=1` is correctly set by second `rig.open()`

**Why VFO_TX works**: in SAT mode, Hamlib maps `RIG_VFO_TX` to the TX VFO (Sub/UL). With `cache.satmode=1` (set by the second `rig.open()`), `ic9700_set_vfo` routes the command correctly. Confirmed with test script `scripts/test_ic9100_hamlib_satmode2.py` (2026-06-20).

#### NET mode satmode detection and transponder selection flow (confirmed 2026-06-17)

**Satmode detection for NET mode**: `HamlibNetController` does NOT query the rig model via rigctld (`_` command). Instead, `is_satmode` property returns:
```python
return self._satmode or self._ctcss_method == "icom_civ"
```
- `_satmode` is always `False` in NET mode (no model ID lookup)
- `ctcss_method == "icom_civ"` is set by user in Rig Settings → this is the definitive indicator

**Why no model name query**: `_fetch_model_name()` (which sent `_` to rigctld) was **removed**. It caused a socket race with the Doppler F/I cycle and was unreliable. If you need to re-add model detection in NET mode, do NOT use `_` command — find another approach.

**Transponder selection flow (NET mode satmode rig)**:
1. User selects transponder → `_apply_transponder_state_to_rig()` in `main_window.py`
2. `set_transponder_freqs(dl_hz, ul_hz)` → sets `_is_same_band` flag
3. `set_current_modes(dl_mode, ul_mode)` → stores DL mode for UL throttle threshold
4. If rig is connected: `_disconnect_rig()` first (user must re-press Connect for new satellite)
5. Background thread: `apply_transponder_state(dl_mode, ul_mode, ctcss_hz)`
   - acquires `_cmd_lock` (pauses Doppler F/I)
   - `_send_split_init_independent()` — **`S 1 Main`（または同バンド時は `S 1 VFOB`）を独立ソケットで先送り**してsatmodeを確立（Direct modeの `set_func(SATMODE,1)` に相当）
   - `send_mode_only()` via independent socket (VFO branch by `_is_same_band`)
   - `_apply_ctcss_civ_direct()` via rigctld TCP commands (`V Sub / L CTCSS_TONE / U TONE / V Main / U TONE 0`)

**順序の根拠**: satmode確立→mode設定→CTCSS設定 の順序はDirect modeと同じ。`S 1 Main` を先に送ることでCTCSSがsatmode確立時にリセットされなくなり、トランスポンダー選択直後にTの字が表示される。`connect()` の `_init_vfo()` が再度 `S 1 Main` を送っても、リグはすでにSATモードに入っているためCTCSS状態をリセットしない。

**Auto-disconnect on satellite change**: when `rig.is_satmode == True` and `rig.is_connected == True`, `_apply_transponder_state_to_rig()` calls `_disconnect_rig()` before re-sending mode/CTCSS. User must manually re-press Connect for the new satellite.

> **動作確認状況（2026-06-20時点）**
> - **Direct モード（IC-9100実機）**: 周波数・モード・CTCSSトーン（クロスバンド・同バンド両方）すべて動作確認済み
>   - SATモード有効化: Hamlib `set_func(RIG_FUNC_SATMODE, 1)` の `open→set_func→close→open` 方式
>   - モード・CTCSS: `_apply_mode_and_ctcss_hamlib()` で Hamlib のみ使用（pyserial 廃止・クロスプラットフォーム対応）
>   - クロスバンドUL: `set_freq(VFO_TX)` 方式で正常書き込み確認済み
>   - SAT ランプが点灯した状態でドップラー補正が正常動作（RS-44・ISS クロスバンドで確認）
>   - 同バンドFM（ISS等）: `_satmode_exit()` 後に `set_mode()` でモードを再設定（`_satmode_exit()` 内の sleep を 0.4s に設定して IC-9100 の内部モード復元を待つ）
>   - 同バンドDL表示を `set_vfo(VFOA)` で確実に復元（`set_freq(VFOA)` では不可）
>   - 同バンドDL更新も 2000 Hz / 60 秒で間引き（`_last_dl_update_time` 管理）
>   - HF/VHF クロスバンド（AO-7: 29MHz DL / 145MHz UL）: SAT mode 正常動作確認済み
>   - `satmode_warmup()`: 起動時に直接 `import Hamlib` することで正常動作（`self._hamlib is None` 問題を修正）
> - **NET モード（IC-9100 + rigctld）**: 周波数・モード・CTCSSトーン（クロスバンド・同バンド両方）すべて動作確認済み
>   - トランスポンダー選択時の順序: `_send_split_init_independent()`（S 1 Main）→ `send_mode_only()` → `_apply_ctcss_civ_direct()`（Direct modeと同じ順序。トランスポンダー選択直後にTの字が表示される）
>   - CTCSS: `_apply_ctcss_civ_direct()` が rigctld TCP コマンド（`V Sub / L CTCSS_TONE / U TONE / V Main / U TONE 0`）を送信（pyserial 廃止・macOS でも動作）
>   - HF/VHF クロスバンド（AO-7: 29MHz DL / 145MHz UL）: SAT mode 正常動作確認済み

**`_freq_band()` のバンド分類（クロスバンド判定に使用）**:
| 周波数範囲 | 戻り値 | 例 |
|---|---|---|
| < 30 MHz | `"HF"` | AO-7 DL 29MHz |
| 30–300 MHz | `"VHF"` | 145MHz（2m） |
| 300–3000 MHz | `"UHF"` | 435MHz（70cm） |
| 3000 MHz 以上 | `"SHF"` | — |

> **注意**: 旧実装は 200MHz 未満をすべて `"VHF"` に分類していたため、HF(29MHz) と VHF(145MHz) が同バンドと誤判定され、satmode が解除されて Main/Sub が入れ替わるバグがあった（AO-7で発覚・修正済み）。

#### CTCSS / Mode setting — IC-9100 / IC-9700 / IC-910H / IC-821H (Direct mode and NET mode)

**Hamlib でモード・CTCSS 両方とも動作する**（2026-06-20 IC-9100 実機で確認）。
旧来の pyserial raw CI-V アプローチは廃止し、全て Hamlib コマンドに統一した。

**なぜ Hamlib で動くか**:
- `set_mode(mode, 0, RIG_VFO_MAIN/RIG_VFO_SUB)` → icom バックエンドが `07 D0/D1` + `06` CI-V を正しく生成
- `set_vfo(VFO_SUB)` + `set_ctcss_tone(VFO_SUB, deci_hz)` + `set_func(FUNC_TONE, 1)` → CI-V `07 D1` + `1B 00 <BCD>` + `16 42 01` を生成
- 各モデル固有の CI-V コマンドは Hamlib バックエンドが自動選択（IC-910H の `1A 07 01` 等）

**`_apply_mode_and_ctcss_hamlib(dl_mode, ul_mode, ctcss_hz)`** — Direct mode の中心実装:
1. `import Hamlib as _H` を直接実行（`self._hamlib` は connect() 前は None なので使えない）
2. `rig.open()` → `set_func(RIG_FUNC_SATMODE, 1)` → `rig.close()` — satmode ON を送信
3. `rig2.open()` — 2回目の open で `cache->satmode=1` が確立（これがないと `VFO_MAIN/SUB` が拒否される）
4. `set_mode(dl_hamlib, 0, VFO_MAIN)` + `set_mode(ul_hamlib, 0, VFO_SUB)` — DL/UL モード設定
5. `set_vfo(VFO_SUB)` → `set_ctcss_tone(VFO_SUB, deci_hz)` → `set_func(FUNC_TONE, 1/0)` — Sub CTCSS
6. `set_vfo(VFO_MAIN)` → `set_func(FUNC_TONE, 0)` — Main CTCSS クリア（ブリード防止）
7. `rig2.close()`
- 全体を `_port_lock` で保護（connect() との競合を防ぐ）

**Direct mode — `set_ctcss_tone(tone_hz)`**:
  - satmode + **not connected** → `_apply_mode_and_ctcss_hamlib()` を呼ぶ（Hamlib 直接、port free）
  - satmode + **connected** → deferred（Hamlib がポートを保持中。`_satmode_enter` が apply 済み）
  - non-satmode → standard Hamlib `set_ctcss_tone` / `set_func` path（FTX-1F, FT-991A 等）

**`_port_lock` — `_apply_mode_and_ctcss_hamlib` と `connect()` の競合防止**:

`HamlibDirectController` の `_port_lock = threading.Lock()` が以下を順序保証する:
- `_apply_mode_and_ctcss_hamlib()` 全体（open→set_func→close→open→[mode/ctcss]→close）
- `connect()` 内の `rig.open()`
- `send_mode_only()` 内の `rig.open()` / `rig.close()`

**NET mode — `_apply_ctcss_civ_direct(tone_hz)`**:
pyserial を廃止し、独立した rigctld TCP ソケットでコマンドを送信（macOS でも動作）:
```
V Sub                    # VFO Sub を選択
L CTCSS_TONE <deci_hz>  # CTCSS 周波数設定（デシ Hz 整数）
U TONE 1/0              # CTCSS エンコーダー ON/OFF
V Main                   # VFO Main を復元
U TONE 0                 # Main の CTCSS クリア（ブリード防止）
```

**IC-9100 mode behaviour — key facts**:
- Entering SAT mode: IC-9100 does **not** unconditionally reset to FM. It generally preserves the mode from the previous session.
- Exiting SAT mode (`set_func(SATMODE, 0)`): IC-9100 **does** restore its "normal-mode memory" (typically USB). This is why `_satmode_exit()` calls `self.set_mode()` after `set_split_vfo()` — to force the transponder's correct DL/UL modes. Sleep after `set_func(SATMODE, 0)` is **0.4s** (increased from 0.1s) to wait for IC-9100's internal mode restoration before applying modes.

**Direct mode — Connect ボタンは常にバックグラウンドスレッドで実行**: `_on_connect_rig1()` は `rig.connect()` を `threading.Thread` で別スレッドに移す。UI スレッドは「Connecting...」表示のまま待機し、完了後に `_rig1_connect_done: Signal = Signal(bool)` 経由で `_finish_rig1_connect()` に通知されてボタン・ステータスを更新する。この変更以前は UI スレッドで同期的に `connect()` を呼んでいたため、IC-9100 の SATMODE 設定に数秒かかる際にウィンドウがフリーズし、キューに溜まったクリックイベントで二重接続が発生していた。

**Direct mode — When CTCSS button is pressed while connected (Doppler running)**: port is held by Hamlib. `_on_ctcss_send()` in `main_window.py` takes a special path for `HamlibDirectController` + `_satmode=True` + `is_connected=True`:
1. `_disconnect_rig()` on UI thread (releases port)
2. Background thread: `set_ctcss_tone(tone_hz)` → `_apply_mode_and_ctcss_hamlib()` (`_port_lock` acquired)
3. Background thread: `rig.connect()` (waits for `_port_lock` to be released before `rig.open()`)
4. `QMetaObject.invokeMethod(self, "_on_satmode_rig_reconnected", QueuedConnection)` to refresh UI on UI thread

**NET mode — CTCSS is sent as part of `apply_transponder_state()`**: no separate disconnect/reconnect needed. See NET mode transponder selection flow above.

**pyserial availability**: pyserial は FT-991A Direct モード（`_apply_mode_and_ctcss_cat_ft991`）と FTX-1F NET モード（`_send_direct_cat`）で引き続き使用。`main.py` の sys.path surgery 前に事前 import が必要:
```python
with contextlib.suppress(Exception):
    import serial as _serial_preload  # noqa: F401
if _HAMLIB_SYS in sys.path:
    sys.path.remove(_HAMLIB_SYS)
```

**FT-991A / FTX-1F are completely unaffected**: they use `_CAT_CTCSS_METHODS` (checked first in `_on_ctcss_send`) and never reach the satmode Hamlib path.

**`_SATMODE_RIG_IDS`** (src/rig/controller.py):
```python
_SATMODE_RIG_IDS: frozenset[int] = frozenset({
    3081,  # IC-9700
    3068,  # IC-9100
    3044,  # IC-910H
    3034,  # IC-821H
})
```

#### Same-band duplex (V/V, U/U) — Direct mode

IC-9100/9700 のサットモードは **Main と Sub を必ず異なるバンドに割り当てる** ハードウェア制約がある。
ISS APRS (145.825 MHz UL/DL 同一) や AO-91 (435 MHz UL/435 MHz DL 同一) などの同バンド衛星では satmode が使えない。

`_freq_band(hz)` で DL と UL のバンドを比較し、同一の場合は **`_is_same_band = True`** と判定して自動的に分岐する：

| 条件 | VFO割り当て | 周波数更新方式 |
|---|---|---|
| **クロスバンド** (V/U, U/V) | satmode (Main=RX, Sub=TX) | `set_freq(RIG_VFO_MAIN, dl)` + `set_freq(RIG_VFO_TX, ul)` |
| **同バンド** (V/V, U/U) | 通常split (VFO-A=RX, VFO-B=TX) | `set_freq(RIG_VFO_A, dl)` + `set_freq(RIG_VFO_B, ul)` + `set_vfo(VFOA)` |

**同バンド時の処理フロー** (`_set_vfo_frequencies_locked`):
1. `_is_same_band == True` を検出
2. `_satmode_active == True` ならば `_satmode_exit()` を呼んでサットモードを解除（SAT MODE OFF → split ON）
3. 以降は VFO-A/B の通常 split でドップラー補正

**`_satmode_exit()`**:
- `self._rig.set_func(RIG_FUNC_SATMODE, 0)` で SAT モードを OFF
- `time.sleep(0.4)` — IC-9100 の内部 normal-mode memory 復元（通常 USB）を待つ。0.4s 未満だと set_mode(FM) が USB で上書きされるレースが発生する
- `set_split_vfo(RIG_VFO_CURR, 1, RIG_VFO_B)` で通常 split (VFO-B=TX) を有効化
- `set_mode(dl_mode, VFOA)` + `set_mode(ul_mode, VFOB)` でトランスポンダーのモードを再設定
- `_satmode_active = False` にセット（finally ブロック内でセットするため、例外時も確実に解除される）

**UL更新頻度（同バンドFM）**: IC-9100 は VFO-B 切り替え時に表示がちらつく。FM/AFSK の場合はキャプチャーレンジ (±5 kHz) が ISS 最大ドップラー (±3.5 kHz at 145 MHz) を上回るため、UL 更新を間引く:
- 閾値: 2000 Hz 以上の変化、または前回更新から 60 秒経過（FM/AFSK）
- 非 FM は 20 Hz / 15 秒

**DL更新頻度（同バンドFM）**: DL も同じ閾値（2000 Hz / 60 秒）で間引く（`_last_dl_update_time` で管理）。UL と同様に FM キャプチャーレンジで十分なため。

**UL更新後のVFO-A表示リストア**: `set_freq(RIG_VFO_B, ul_hz)` 後、Hamlibのicomバックエンドは内部のCURRをVFO-Bのままにするため、IC-9100のディスプレイがUL周波数を表示し続ける。UL更新が完了するたびに `rig.set_vfo(rx_vfo)` を呼び、CI-V `07 00`（VFO-A選択）を送信してDL表示に戻す。`set_freq(VFOA, hz)` では効果がないことが実機確認済み（周波数書き込みのみでディスプレイ切り替えは行われない）。

**モード設定 (`send_mode_only`)**: `_satmode_active` フラグで VFO を選択
- `_satmode_active == True` → `RIG_VFO_MAIN` / `RIG_VFO_SUB`（旧 `SUB_A` は satmode で拒否されるため修正済み）
- `_satmode_active == False`（同バンド）→ `RIG_VFO_A` / `RIG_VFO_B`

**同バンド時の CTCSS**: `HamlibDirectController.set_ctcss_tone()` は `self._satmode == True` であれば `_satmode_active` の状態（cross-band / same-band）に関わらず常に `_apply_mode_and_ctcss_hamlib()` を呼ぶ。したがって同バンド衛星でも Hamlib 経由でトーンが正しく設定され、動作する（実機確認済み）。

#### Same-band duplex (V/V, U/U) — NET mode

NET mode の同バンド対応は Direct mode と同じロジックで `_is_same_band` フラグによる分岐。

`set_transponder_freqs(dl_hz, ul_hz)` で `_is_same_band` を設定（Connect 前のトランスポンダー選択時）:
```python
self._is_same_band = self._freq_band(dl_hz) == self._freq_band(ul_hz)
```

| 条件 | split init (`_init_vfo`) | send_mode_only VFO |
|---|---|---|
| **クロスバンド** (V/U, U/V) | `S 1 Main`（rigctld satmode） | Sub/Main |
| **同バンド** (V/V, U/U) | `S 1 VFOB`（通常 split） | VFOB/Main (VFOA相当) |

**UL 更新頻度（同バンド）**: IC-9100 は `I` コマンド（VFOB 更新）時にディスプレイが一瞬ちらつく。
Direct mode と同じ閾値を NET mode にも適用（`_last_ul_update_time` + `_current_dl_mode` で管理）:
- FM / AFSK / DIGITALVOICE: 2000 Hz 以上の変化、または前回更新から 60 秒経過
- SSB / CW など非 FM: 20 Hz / 15 秒

残る 2フラッシュ（約1分ごとのUL更新時）は IC-9100 ハードウェアの動作（VFOB 更新直後に一瞬 VFOB 表示 → VFOA 表示に戻る）であり、ソフトウェアバグではない。

### NET mode (rigctld) vs Direct mode (Hamlib built-in)
- FTX-1F: both NET and Direct work; NET preferred (more stable)
  - Direct: `_apply_mode_and_ctcss_cat_ftx1()` — `MD1{ul}/MD0{dl}` via `os.open()`, `CN1/CT1` for CTCSS
  - NET: uses independent socket for mode/CTCSS commands to avoid Doppler cycle conflict
- FT-991 / FT-991A (models 1035/1036): both NET and Direct work (Direct confirmed 2026-06-18)
  - Direct: `_apply_mode_and_ctcss_cat_ft991()` — `SV;MD0{ul};SV;MD0{dl}` via pyserial, `CN0/CT0` for CTCSS
    - pyserial 使用（FTX-1F の `os.open()` と異なりボーレート設定が確実）
    - CTCSS は SV スワップ不要（`CT02` は TX-VFO にグローバル適用）
  - NET: `ctcss_method == "ft991"` で識別。`send_mode_only()` が SV スワップを行う。`send_ctcss_cat()` は SV スワップなし
  - `_FT991_DIRECT_MODEL_IDS = frozenset({1035, 1036})`。main_window.py では `_FTX1_MODEL_IDS | _FT991_DIRECT_MODEL_IDS` を一括判定
- IC-9700 / IC-9100 / IC-910H / IC-821H (satmode rigs): both NET and Direct work (confirmed 2026-06-20, cross-band and same-band)
  - NET satmode detection: `ctcss_method == "icom_civ"` (user setting) — model name query removed
  - NET mode + CTCSS: rigctld TCP commands (`V Sub / L CTCSS_TONE / U TONE / V Main`) — pyserial 廃止、macOS でも動作
  - NET mode + same-band: `S 1 VFOB` instead of `S 1 Main`; UL throttled to reduce display flicker
  - **IC-910H / IC-821H**: Hamlib がモデル固有 CI-V を自動選択するため同一コードパスで動作するはず（実機未確認）
- Detection: use `ctcss_method` setting value (`"ft991"`, `"icom_civ"`, `"hamlib"`) — **never** use `w ID;` or rigctld `_` command (causes 10 s timeout and socket race)

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

---

## SDR 機能設計方針（2026-06-08 確定）

### バックエンド

**SoapySDR** を採用。RTL-SDR・HackRF・Airspy 等の多機種対応。
- Python binding は pip 非対応 → システムパッケージ経由またはバンドル版を使用
- `SoapySDR` が import できない場合は SDR 機能を自動非表示（graceful degradation）
- デバイス列挙: `SoapySDR.Device.enumerate()` / 未インストール時は `pyusb` で USB VID/PID スキャン

#### Windows バンドル構成（v0.1.4 以降・CI で conda-forge から自動取得）

Windows インストーラーには SoapySDR 0.8.1 と以下のデバイスモジュールが同梱されている。
ユーザーは追加インストール不要。RTL-SDR と ADALM-Pluto（USB接続時のみ）は Zadig で WinUSB ドライバを一度当てる必要がある。

| 同梱モジュール | 対応デバイス | Zadig 必要 | 入手方法 |
|---|---|---|---|
| SoapyRTLSDR | RTL-SDR（RTL2832U 系全般） | ✓ 一回限り | conda-forge |
| SoapyHackRF | HackRF One | — | conda-forge |
| SoapyAirspy | Airspy R2 / Airspy Mini | — | conda-forge |
| SoapyAirspyHF | Airspy HF+ Discovery | — | conda-forge |
| SoapyPlutoSDR | ADALM-Pluto | USB時のみ | MSVC ソースビルド（CI） |

ADALM-Pluto はネットワーク接続（IP: 192.168.2.1）でも動作し、その場合 Zadig 不要。

バンドル DLL の配置: core DLL + Python binding は `_MEIPASS/`、モジュール DLL は `_MEIPASS/soapy_modules/`。
起動時に `SOAPY_SDR_PLUGIN_PATH=soapy_modules/` をセット（`src/main.py` の frozen ブロック）。

conda-forge パッケージ取得スクリプト: `scripts/extract_soapy_conda.py`（CI の Windows ビルドステップで実行）。
SoapyPlutoSDR は conda-forge に存在しないため CI で MSVC ソースビルドし `soapy-win64/modules/` に配置する。

#### Linux / macOS インストール方法

| OS | コマンド |
|---|---|
| Ubuntu | `sudo apt install python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf soapysdr-module-airspy` |
| macOS | `brew install soapysdr soapyrtlsdr soapyhackrf soapyairspy` |

---

#### PlutoSDR（ADALM-Pluto）Windows バンドル実装メモ（v0.1.5 で実装・CI 緑確認済み）

**背景**: SoapyPlutoSDR は conda-forge に存在しないためソースビルドが必要。
CI の "Build SoapyPlutoSDR for Windows" ステップで実装済み（`v0.1.5`、2回の修正で緑確認）。

##### 依存関係と実際の入手方法

| ライブラリ | 入手方法 | 備考 |
|---|---|---|
| libiio | conda-forge win-64（`libiio>=0.26`）| DLL + ヘッダー両方取得できる |
| libad9361 | 不要 | 260 kHz 以上のサンプルレートなら動作。アマチュア衛星用途には十分 |
| SoapyPlutoSDR | `pothosware/SoapyPlutoSDR` ソースビルド（MSVC + Ninja） | 出力は `PlutoSDRSupport.dll` |

##### libad9361 の役割（省略している理由）

`libad9361` は低サンプルレート時に AD9361 チップへ FIR フィルターを自動ロードするライブラリ。

| 状態 | 最低サンプルレート | 影響 |
|---|---|---|
| libad9361 **あり** | 約 65 kHz（25 MHz ÷ 384） | 低レートでも FIR で品質維持 |
| libad9361 **なし** | 約 260 kHz（25 MHz ÷ 96） | 260 kHz 以上なら通常動作 |

アマチュア衛星用途（FM/SSB/CW・IQ録音）は 260 kHz 以上で十分なため省略。

##### CI 実装（.github/workflows/ci.yml）

"Bundle SoapySDR for Windows" ステップの直後に配置。

**重要な落とし穴（v0.1.5 デバッグで判明）**:

1. **conda cmake が PATH に入り VS 検出に失敗する問題**
   - conda で cmake をインストールすると conda のパスが優先され、VS を見つけられない cmake が使われる
   - **対策**: conda には cmake を含めない。システム cmake をフルパス `C:\Program Files\CMake\bin\cmake.exe` で指定。VS ジェネレーター (`"Visual Studio 17 2022"`) を使わず、`vcvarsall.bat` で MSVC を PATH に追加してから Ninja ジェネレーターを使う

2. **DLL ファイル名が想定と異なる問題**
   - SoapyPlutoSDR のビルド出力は `SoapyPlutoSDR.dll` ではなく **`PlutoSDRSupport.dll`**（CMake target 名）
   - SoapySDR は `SOAPY_SDR_PLUGIN_PATH` ディレクトリの全 DLL をロードするためファイル名は問わない
   - **対策**: `Get-ChildItem` のフィルターを `PlutoSDRSupport.dll` に設定

**実際に動作するビルド手順（ci.yml のステップ）**:
```powershell
# 1. conda で libiio + soapysdr ヘッダー取得（cmake は含めない！）
conda create --prefix pluto-deps -c conda-forge "soapysdr=0.8.1" "libiio>=0.26"

# 2. vcvarsall で MSVC を PATH に設定（VS ジェネレーターを避けて Ninja を使うため）
$vcvarsall = (vswhere でパス取得)
cmd /c "`"$vcvarsall`" x64 && set" → 環境変数をプロセスに反映

# 3. システム cmake + Ninja でビルド
"C:\Program Files\CMake\bin\cmake.exe" -G Ninja -DCMAKE_PREFIX_PATH=pluto-deps\Library
cmake --build SoapyPlutoSDR-build

# 4. コピー（出力名に注意）
PlutoSDRSupport.dll → soapy-win64/modules/   # ← SoapyPlutoSDR.dll ではない
libiio.dll         → soapy-win64/bin/
```

##### ユーザー側の追加作業

- USB 接続時: WinUSB ドライバーを Zadig で適用（RTL-SDR と同様・一回限り）
- ネットワーク接続時（192.168.2.1）: 追加ドライバー不要

##### BladeRF について

libbladerf は conda-forge win-64 に存在するが SoapyBladeRF はなし。
PlutoSDR と同じアプローチ（MSVC ソースビルド）で追加可能。

**既存環境への対応**: `SoapySDR.Device.enumerate()` が成功すれば即 Ready。追加作業なし。
**排他制御**: SoapySDR デバイスは 1 プロセス占有。Ground-Station 等と同時使用不可。

#### Bias-T ドライバ別キー対応（実装済み・2026-06-09 確定）

`SoapySDR.Device.writeSetting()` は未知のキーを**例外なしに無視する**ため、
try-except で複数キーを試す方式は機能しない。ドライバ名で分岐が必須。

```python
driver = (self._info.driver or "").lower()
if "hackrf" in driver:
    key = "bias_tx"
    value = "true" if enabled else "false"   # HackRF: 文字列 "true"/"false"
elif "rtlsdr" in driver or "rtl" in driver:
    key = "biastee"
    value = "1" if enabled else "0"           # RTL-SDR: 文字列 "1"/"0"
else:
    key = "biastee"
    value = "true" if enabled else "false"    # その他: 汎用フォールバック
```

#### CW 復調方式（エンベロープ検出なし・2026-06-09 確定）

エンベロープ検出（`np.abs()` + LPF）はバンドパスフィルタで帯域制限したノイズにも
必ず正値を返すため、信号がなくてもブーン音が発生する（AGC が増幅）。
CW 復調は**エンベロープ検出を一切行わない**方式を採用：

```
I/Q → DC除去 → 2段デシメーション（~8kHz）→ 実部取り出し → SOS BPF(300-3000Hz) → 出力
```

- ナチュラルオフセット（搬送波が中心周波数±数百〜数千Hz）がそのまま音声になる
- BPF は `scipy.signal.butter(4, [300/nyq, 3000/nyq], btype='band', output='sos')` + `sosfilt()`
  （狭帯域 b,a 形式は数値的に不安定なため SOS 形式必須）
- サイドトーン注入不要

### ディレクトリ構成

```
src/sdr/
├── __init__.py
├── device.py          # SoapySDRDevice — デバイス列挙・接続・サンプル取得
├── pipeline.py        # SDRPipeline (QThread) — pub/sub I/Q配信ハブ
├── demodulator.py     # NFM / USB / LSB / CW 復調（numpy + scipy）
├── recorder.py        # IQRecorder — CF32 WAV 書き出し
├── sdr_state.py       # SdrWebState — Web UI 向け状態共有
└── plugins/
    ├── base.py            # SdrPlugin 抽象基底クラス
    ├── fm_demod.py        # NFM 復調（初期実装）
    ├── ssb_cw_demod.py    # SSB/CW 復調（初期実装）
    ├── iq_recorder.py     # IQ 録音（初期実装）
    ├── satdump.py         # 将来: SatDump 連携（HRPT/LRPT 画像）
    ├── direwolf.py        # 将来: APRS / Direwolf 連携
    ├── wsjtx.py           # 将来: FT4 / WSJT-X 連携
    └── sstv.py            # 将来: SSTV 受信
```

### I/Q パイプライン設計（pipeline.py）

`SDRPipeline` は pub/sub バスとして設計する。各プラグインがサンプルを購読し、
パイプライン本体に触れずにプラグイン追加が可能。

```
SoapySDR（QThread内）
    │  I/Q samples (numpy CF32, 2.4MHz)
    ▼
SDRPipeline
    ├── FFT → スペクトラムデータ → Signal → SDR Control UI（10fps）
    ├── → FM/SSB/CW Demodulator → sounddevice 音声出力
    ├── → IQRecorder → CF32 WAV ファイル
    ├── → [将来] SatDump stdin pipe
    └── → [将来] Direwolf / SSTV AudioSource
```

### AudioSource 抽象層

狭帯域データモード（APRS・SSTV 等）の音声入力元を抽象化する。
デコーダープラグインは `AudioSource` インターフェースのみを見るため、
SDR ソフトウェア復調とリグサウンドカード入力を透過的に切り替えられる。

```python
class AudioSource:  # 抽象基底
    pass

class SdrAudioSource(AudioSource):
    # SoapySDR → ソフトウェア復調 → PCM バッファ

class SoundcardAudioSource(AudioSource):
    # sounddevice.InputStream → PCM バッファ
    # リグの AF 出力が入っているデバイスを sounddevice.query_devices() で列挙して指定
```

- SDR 未接続時は SdrAudioSource オプションをグレーアウト
- リグ未接続時は SoundcardAudioSource オプションをグレーアウト

### SdrPlugin 抽象基底クラス（plugins/base.py）

```python
class SdrPlugin:
    name: str                      # "APRS / Direwolf"
    supported_modes: list[str]     # ["FM", "AFSK"]
    requires_tx_audio: bool        # APRS TX は True
    requires_external: str | None  # "direwolf", "satdump" 等

    def start(self, center_freq_hz: float) -> None: ...
    def stop(self) -> None: ...
    def get_widget(self) -> QWidget: ...  # SDR Control タブ内に埋め込む UI
    def is_available(self) -> bool: ...  # 外部ツール検出
```

### SdrRigAdapter（src/rig/controller.py への追加）

既存の `RigController` 抽象基底クラスを継承し、SDR を Rig 1 / Rig 2 として扱えるようにする。

```
RigController（抽象）
    ├── HamlibDirectController   ← 既存
    ├── HamlibNetController      ← 既存
    └── SdrRigAdapter            ← 新設
          SoapySDRDevice を内包
          is_sdr = True プロパティ
          connect() → SDRPipeline 起動
          set_frequency() → SoapySDR 中心周波数を設定（ドップラー補正連動）
```

### UI 設計

#### Rig Settings ダイアログ — SDR Settings タブ（第3タブ）

- デバイス列挙ボタン（`[Enumerate]`）
- デバイス選択ドロップダウン（SoapySDR.Device.enumerate() の結果）
- Sample Rate / Bandwidth / PPM Offset / RF Gain 設定
- **Assign as: ○ Rig 1  ● Rig 2** ラジオボタン
- IQ 録音保存先ディレクトリ設定

#### Radio Control — SDR 接続時の表示

```
通常の Rig:  [RIG: Connected  ■ 435.612 MHz]
SDR の Rig:  [SDR: Connected  ■ 435.612 MHz]  ← シアン色で区別
```

#### SDR Control タブ（Radio Control タブの隣）

- **SDR 未接続時は `setEnabled(False)`**（グレーアウト）
- 接続後にアクティブ化
- 内部はプラグインホスト構造（将来プラグインがタブとして追加される）

**初期実装のパネル構成:**
```
┌─ Spectrum ──────────────────────────────────────┐
│  QtCharts QLineSeries で約 10fps のリアルタイム FFT │
│  横軸: 周波数, 縦軸: dBFS, 中心周波数マーカー（赤） │
└──────────────────────────────────────────────────┘
┌─ Demodulator ───────────────────────────────────┐
│  Mode: [NFM ▼] [USB] [LSB] [CW]                  │
│  Filter BW: スライダー  Volume: スライダー  AGC    │
│  [▶ Start Audio]  [■ Stop Audio]                 │
└──────────────────────────────────────────────────┘
┌─ IQ Recorder ───────────────────────────────────┐
│  BW: [250 kHz ▼]   ファイル名自動生成             │
│  [● REC]  [■ STOP]   経過時間 / ファイルサイズ    │
└──────────────────────────────────────────────────┘
```

### トランスポンダー選択 → デモジュレーターモード自動切替

Radio Control でトランスポンダーを選択すると SDR Control のモードを自動設定する。

| SATNOGS mode 値 | SDR Control で自動選択 |
|---|---|
| `FM` / `DIGITALVOICE` | NFM |
| `SSB` / `USB` | USB |
| `LSB` | LSB |
| `CW` / `CW-R` | CW |
| `BPSK` / `AFSK` | USB（IQ 録音推奨） |

### IQ 録音ファイル仕様

- フォーマット: WAV CF32（32bit float ステレオ I/Q）
- サンプリングレート: 選択した帯域幅（例: 250 kHz）
- ファイル名: `{NORAD}_{衛星名}_{AOS時刻UTC}.iq.wav`
- SDR#・GQRX・SDR++ 等で直接再生・復調可能

### 将来拡張プラグイン（フェーズ2以降）

#### アマチュア衛星・デジタルモード

| プラグイン | バックエンド | 受信入力 | 送信 |
|---|---|---|---|
| HRPT/LRPT 画像 | SatDump（サブプロセス stdin pipe） | SDR のみ | なし |
| 衛星テレメトリーデコード | gr-satellites（GNU Radio OOT モジュール、サブプロセス） | SDR のみ | なし |
| APRS | Direwolf（TCP KISS） | SDR or Rigサウンドカード | Rig サウンドカード + PTT |
| FT4 | WSJT-X（UDP 連携） | SDR or Rigサウンドカード | Rig サウンドカード + PTT |
| CW デコード | AI-CW デコーダー（内蔵、ML推論） | SDR or Rigサウンドカード | なし |
| SSTV 受信 | pySSTV（内蔵） | SDR or Rigサウンドカード | なし |

外部ツール（SatDump・Direwolf・WSJT-X・gr-satellites）はサブプロセス起動。内部実装しない。

#### 業務用衛星受信（フェーズ2以降・計画中）

HackRF / RTL-SDR + 適切な LNA・フィルターで受信可能な業務用衛星信号のデコードを追加予定。
いずれもオープンソースのデコーダーが存在し、SDR プラグインとして組み込める。

| 衛星システム | 周波数帯 | 内容 | 主なOSSデコーダー候補 |
|---|---|---|---|
| **Inmarsat-C（STD-C）** | 1.5 GHz L帯 | 海事安全情報（MSI）・EGC（Enhanced Group Call）・LRIT | [aero](https://github.com/jontio/JAERO)・[inmarsat-c](https://github.com/Outernet-Project/aero) |
| **Cospas-Sarsat L帯下り** | 1544.5 MHz | 捜索救助ビーコン位置情報（PLB/EPIRB/ELT） | [LRPT decoder](https://github.com/opensatelliteproject)・gr-satellites |
| **Iridium L帯 ACARS** | 1616〜1626.5 MHz | 航空 ACARS メッセージ・衛星電話傍受（表示のみ） | [iridium-toolkit](https://github.com/dholm/iridium-toolkit) |
| **Orbcomm** | 137〜138 MHz VHF | IoT/M2M データメッセージ・AIS 補完 | [gr-orbcomm](https://github.com/dholm/gr-orbcomm)・[orbcomm-decoder](https://github.com/microp11/orbcomm) |
| **みちびき（QZSS）データ放送** | 1278.75 MHz L6帯 | 高精度測位補強（MADOCA-PPP）・災害危機管理通報 | [qzsl6tool](https://github.com/yoronneko/qzsl6tool) |

**実装方針:**
- 各デコーダーはサブプロセスとして起動し、stdout/パイプ経由でデコード結果を受け取る
- 専用 UI パネルを SDR Control タブ内のプラグインタブとして追加
- IQ 録音ファイルからのオフライン再解析にも対応予定
- ライセンスに注意: 各国の電波法規制を遵守すること（受信のみ・復号結果の二次利用不可の場合あり）

#### gr-satellites について
- GNU Radio の OOT（Out-Of-Tree）モジュール。100 機種以上のアマチュア衛星テレメトリーフォーマットに対応
- `gr_satellites` コマンド（CLI）を IQ ストリームに繋いでサブプロセス起動する方式が最も移植性が高い
- インストール: Linux は `pip install gr-satellites`（GNU Radio 3.10 以上が前提）
- SDR Device Installation ダイアログに gr-satellites のインストール状態確認・誘導を追加予定

#### AI-CW デコーダーについて
- 候補: **morse-decoder**（PyTorch CNN ベース）・**DeepMorse**・**cwdecoder**（RNN）など
- 従来のゼロクロス検出方式より S/N 比の低い信号でも高精度にデコード可能
- 内蔵実装方針: sounddevice または SdrAudioSource から 8kHz PCM を取得 → Python 内で推論
- モデルファイル（数 MB 程度）はアプリバンドルに同梱するか、初回起動時に自動ダウンロード
- ライセンスに注意（MIT または Apache 2.0 のモデルを選定すること）

### SDR Device Installation ダイアログ（Help メニュー）

- USB VID/PID スキャン（`pyusb`）でデバイスを識別
- SoapySDR インストール状態を表示
- **Linux**: `pkexec apt-get install` でボタン操作による自動インストール
- **Windows**: PothosSDR はブラウザでダウンロードページを開く（`QDesktopServices.openUrl`）、Zadig は直リンク `.exe` をダウンロードして起動。いずれもウィザード操作はユーザーが手動で行う
- **macOS**: Homebrew があれば `brew install` を自動実行
- すでにインストール済み環境では即 `✅ Ready` 表示

### 依存パッケージ（optional）

```toml
[project.optional-dependencies]
sdr = [
    "pyusb>=1.2",         # USB VID/PID スキャン（SDR Device Installation 用）
    "scipy>=1.12",        # DSP フィルタ・ヒルベルト変換
    "sounddevice>=0.4",   # 音声出力（PortAudio ラッパー）
]
# SoapySDR はシステムパッケージ経由のため pip dependencies に含めない
```

---

## Communications 機能設計方針（2026-06-12 確定・v0.2.0 基本実装済み）

### 概要

メニューバーに **Communications** メニューを新設し（Radio と Autotrack/Record の間）、
APRS・FT4・SSTV 等のデジタル通信機能をサブメニューとして追加していく。
各機能は専用タブとして開き、× ボタンで個別にクローズできる非常駐タブとして実装する。

**メニュー構成:**
```
File / Satellite / Radio / Communications / Autotrack/Record / View / Help
                              └── APRS
                              └── FT4（将来）
                              └── SSTV（将来）
```

---

### APRS 機能設計（v0.2.0 目標）

#### ディレクトリ構成

```
src/
├── comms/
│   ├── __init__.py
│   ├── aprs/
│   │   ├── __init__.py
│   │   ├── engine.py          # APRSEngine — KISS TCP 接続・フレーム送受信
│   │   ├── parser.py          # AX.25 / APRS フレームパーサー（位置・メッセージ）
│   │   ├── afsk_demod.py      # Bell 202 AFSK 復調器（SDR 用純 Python 実装）
│   │   └── direwolf.py        # Direwolf サブプロセス管理
│   └── ft4/                   # 将来
```

#### 全体アーキテクチャ

```
[SDR Connect 時]
SDRPipeline の I/Q → afsk_demod.py（numpy/scipy）→ AX.25 パーサー → APRSEngine

[Rig Connect 時（サウンドカード設定済み）]
sounddevice IN → Direwolf stdin（ADEVICE stdin stdout）
Direwolf stdout → sounddevice OUT（TX 音声）
Direwolf KISS TCP :8001 → APRSEngine
```

#### 入力ソース自動切替ルール

| Rig Control の状態 | APRS 入力ソース | 送信可否 |
|---|---|---|
| SDR Connect のみ | SDR（Python 復調） | 不可（受信専用） |
| Rig Connect のみ（Sound Card 設定済み） | サウンドカード + Direwolf | 可（PTT あり） |
| 両方 Connect | Rig 優先（送信できる方） | 可 |
| どちらも未接続 | — | APRS タブを開かない |

入力ソースは APRS タブ内に「表示のみ」で示す（ユーザーが選択するものではない）。

#### Direwolf 検出・バンドル方針

検出の優先順位:
1. ユーザーインストール版（`Help > Direwolf...` でインストールしたもの）
2. システムインストール版（`which direwolf` / PATH）
3. バンドル版（アプリに同梱）

インストール先（ユーザーインストール版）:
```
Linux:   ~/.local/share/gpredict-improved/direwolf/
macOS:   ~/Library/Application Support/gpredict-improved/direwolf/
Windows: %APPDATA%/gpredict-improved/direwolf/
```

Direwolf は `ADEVICE stdin stdout` モードで起動するため、ALSA / PortAudio への依存なし。
バンドルビルドは CI で各プラットフォーム向けにソースビルドし GitHub Releases にアップロード。

`Help > Direwolf...` ダイアログ:
- 現在使用中の Direwolf パス・バージョンを表示
- 未インストール時はプラットフォーム別インストール支援
  - Linux: `apt install direwolf` コマンドをクリップボードにコピー or `pkexec` 自動実行
  - Windows: GitHub Releases から `.zip` をダウンロード
  - macOS: `brew install direwolf` をターミナルで実行
- 常時: バンドル版を最新版に更新するボタン

#### PTT 制御（Direwolf 使用時）

Direwolf の PTT は `NONE` に設定し、アプリ側が Hamlib CAT 経由で制御する。

```
送信直前: Doppler 補正済み UL 周波数を確定・CAT でリグにセット
PTT ON:  RigController.set_ptt(True)（CAT コマンド）
         Direwolf が音声送出（約 0.3〜0.5 秒）
PTT OFF: RigController.set_ptt(False)
         Doppler 補正ループを再開
```

送信中（約 0.5 秒）のドップラー変化は 5〜10 Hz 程度で無視できるため、
送信中は Doppler 補正ループを停止し、周波数変更を禁止する。

シリアル RTS/DTR による PTT は将来の後付けオプションとして保留。

#### SDR 純 Python 復調パイプライン（受信専用）

Bell 202 AFSK（1200 baud、マーク 1200 Hz / スペース 2200 Hz）を numpy + scipy で復調する。
CW 復調の既存パイプラインを流用できる。

```
SDRPipeline の I/Q（~48kHz にデシメーション）
    → バンドパスフィルタ（900〜2500 Hz、SOS 形式）
    → mark/space 電力比較（ゴートツェルフィルタ or Hilbert 変換）
    → ビットスライサー（1200 baud クロック同期）
    → HDLC フレーム同期・フラグ検出
    → AX.25 フレームデコード
    → APRS パーサー（位置・メッセージ・テレメトリー）
```

AX.25 テレメトリーを送る衛星（FUNcube 等）も同じパイプラインで受信可能。

#### APRS タブ UI 設計

**タブの開閉:**
- `Communications > APRS` クリックで開く（非常駐）
- タブ右上の × ボタンでクローズ
- クローズ時: Direwolf 停止・KISS TCP 切断・SDR 復調停止（Rig/SDR 接続は維持）
- Rig/SDR どちらも未接続の場合はクローズ状態を維持（タブを開かない）
- 常駐タブ（Dashboard 等）は × を非表示にする（`tabBar().setTabButton(index, position, None)`）

**レイアウト:**
```
┌─ APRS ──────────────────────────────────────────────────── × ┐
│ Callsign: [JF9SOM  ] SSID: [-9▼] Via: [ARISS          ]      │
│ Input: SDR (HackRF One)  ← 自動検出・表示のみ                  │
├──────────────────────────────────────────────────────────────┤
│ 受信ログ（タイムスタンプ / コールサイン / 内容）                  │
│  14:23:01  JA1XYZ > APRS,ARISS*: Hello from Tokyo            │
│  14:22:45  W1ABC  > APRS,ARISS*: [位置情報あり → 地図ピン]     │
├──────────────────────────────────────────────────────────────┤
│ To: [JA1XYZ      ]  Message: [                    ]  [Send]  │
│ （Send は Rig Connect 時のみ有効・SDR 受信専用時はグレーアウト） │
└──────────────────────────────────────────────────────────────┘
```

**設定の保存:** コールサイン・SSID・Via パスは `app_settings` に保存（再起動後も維持）。

#### Dashboard 地図への位置表示

位置情報を含む APRS パケットを受信した場合、Dashboard のズームマップに局ピンを表示する。

- ピンにコールサイン ラベルを付ける
- 衛星ドットとは異なる色・形状（例: ▲マーカー）で区別する
- タブクローズ時にピンをクリア

#### データ永続化

既存の SQLite DB に `aprs_log` テーブルを追加:

```sql
CREATE TABLE aprs_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  DATETIME NOT NULL,
    callsign     TEXT NOT NULL,
    via          TEXT,
    latitude_deg REAL,
    longitude_deg REAL,
    comment      TEXT,
    raw_frame    TEXT,
    norad_sat    INTEGER   -- パス中に受信した衛星の NORAD ID（任意）
);
```

#### Rig Settings — Sound Card タブ（第4タブ）

既存の Rig Settings ダイアログに Sound Card タブを追加する。
APRS だけでなく将来の FT4・SSTV 等でも共用する音声 I/O 設定。

| 設定項目 | 内容 |
|---|---|
| 入力デバイス | sounddevice で列挙したデバイス一覧から選択 |
| 出力デバイス | 同上 |
| サンプルレート | 48000 Hz 固定（Direwolf デフォルト） |
| テストボタン | ループバックテストで設定確認 |

Sound Card タブが未設定の場合、Rig Connect 時も Direwolf を起動しない。
（APRS タブの Input 欄に「Sound Card not configured」と表示）

#### ADIF ログ出力

送受信した全 QSO を ADIF（.adi）形式でエクスポートできる。

**保存タイミング**: 送受信のたびに SQLite `aprs_log` テーブルにリアルタイム保存。
.adi ファイルへの書き出しはエクスポートボタン押下時のみ。

**ADIF フィールド:**

| フィールド | 内容 |
|---|---|
| `CALL` | 相手コールサイン |
| `QSO_DATE` | 日付（YYYYMMDD UTC） |
| `TIME_ON` | 時刻（HHMMSS UTC） |
| `BAND` | 使用バンド（例: 2m） |
| `MODE` | APRS |
| `FREQ` | 使用周波数（MHz、Radio Control のトランスポンダーから取得） |
| `COMMENT` | メッセージ内容 |
| `SAT_NAME` | 衛星名（ISS 等） |
| `PROP_MODE` | `SAT`（衛星経由を示す ADIF 標準値） |
| `MY_GRIDSQUARE` | 自局グリッドロケーター |
| `GRIDSQUARE` | 相手局グリッドロケーター（位置情報があれば） |

**エクスポートボタン**: タブ下部に配置。保存済み QSO 件数を隣に表示。
ファイル名は `aprs_log_YYYYMMDD.adi` で保存ダイアログを表示する。

---

### Telemetry タブ設計（v0.2.0 目標・APRS と同時実装）

#### 概要

AX.25 フレームを受信し、衛星ごとのフォーマット定義に従ってテレメトリー値を表示する。
APRS とはアプリ層が異なるが、物理層・データリンク層（Bell 202 AFSK + AX.25）は共通のため
APRS の復調パイプラインを流用する。

**メニュー位置**: `Communications > Telemetry`（APRS の次）

#### 対応範囲（v0.2.0）

| 対応 | 内容 |
|---|---|
| ✅ | 1200 baud Bell 202 AFSK 衛星（AX.25） |
| ✅ | APRS 形式ペイロード（位置・テレメトリー） |
| ✅ | JSON 定義ファイルによる独自バイナリ形式の解釈 |
| ✅ | 定義なし衛星の生 hex 表示 |
| ❌ | 9600 baud G3RUH FSK（後回し） |
| ❌ | gr-satellites 連携（後回し） |

#### gr-satellites 連携（将来）

gr-satellites は GNU Radio が必須依存のためバンドル・自動インストールは行わない。
将来的に以下を追加する:
- システムへのインストール済みを自動検出（Direwolf と同じ方式）
- `Help > gr-satellites...` でインストール案内（apt / Homebrew のコマンド表示のみ）
- GNU Radio / gr-satellites が検出された場合のみ 9600 baud 衛星等の拡張デコードを有効化

#### タブ UI 設計

**開閉**: `Communications > Telemetry` クリックで開く。× で閉じる（非常駐・APRS と同じ）

**衛星・トランスポンダー選択**: Radio Control タブで選択中のものを自動参照（APRS と共通）

**レイアウト:**
```
┌─ Telemetry ────────────────────────────────────── × ┐
│ Satellite: JO-97 (43803)   Input: SDR (HackRF One)   │
├──────────────────────────────────────────────────────┤
│ 受信ログ                                              │
│  14:23:01  JO-97  battery_v: 3.82V  temp_c: 24.1°C  │
│  14:22:45  JO-97  [raw] A3 F2 00 1B 44 ...           │
├──────────────────────────────────────────────────────┤
│ [Export CSV...]                  Frames: 18 received │
└──────────────────────────────────────────────────────┘
```

**エクスポート**: CSV 形式（フィールドが衛星ごとに異なるため ADIF より CSV が適切）
ファイル名: `telemetry_{衛星名}_{YYYYMMDD}.csv`

#### フォーマット定義ファイル（JSON）

`src/data/telemetry_formats/{norad_cat_id}.json` に衛星ごとに配置。
アプリ同梱で主要 1200 baud アマチュア衛星を順次追加していく。

```json
{
  "norad": 43803,
  "name": "JO-97",
  "callsign": "JO-97",
  "modulation": "AFSK1200",
  "ax25_pid": "0xF0",
  "fields": [
    {"name": "battery_v",  "offset": 0, "length": 2,
     "type": "uint16_be", "scale": 0.001, "unit": "V"},
    {"name": "temp_c",     "offset": 2, "length": 2,
     "type": "int16_be",  "scale": 0.1,  "unit": "°C"},
    {"name": "tx_power_mw","offset": 4, "length": 2,
     "type": "uint16_be", "scale": 1.0,  "unit": "mW"}
  ]
}
```

**フィールド型一覧**: `uint8`, `int8`, `uint16_be`, `uint16_le`, `int16_be`, `int16_le`,
`uint32_be`, `float32_be`, `ascii`

定義ファイルがない衛星は AX.25 フレームのコールサイン・ペイロードを生 hex で表示する。

#### データ永続化

```sql
CREATE TABLE telemetry_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at   DATETIME NOT NULL,
    norad_cat_id  INTEGER,
    callsign      TEXT NOT NULL,
    raw_hex       TEXT NOT NULL,
    parsed_json   TEXT,          -- JSON 定義でデコードした値（JSON 文字列）
    signal_db     REAL           -- 受信時の信号強度（取得できれば）
);
```

#### 入力ソース自動切替（APRS と同じルール）

| Rig Control の状態 | Telemetry 入力ソース |
|---|---|
| SDR Connect | SDR（Python 復調・受信専用） |
| Rig Connect（Sound Card 設定済み） | サウンドカード + Direwolf |
| どちらも未接続 | タブを開かない |

#### 復調パイプライン共有

APRS と Telemetry は同じ Bell 202 AFSK 復調器・AX.25 デコーダーを共用する。
両タブが同時に開かれている場合、復調器は一つだけ起動しフレームを両方に配信する
（pub/sub パターン）。
