"""
Web モジュール — FastAPI REST API + WebSocket サーバー

create_app()  → FastAPI アプリ生成（テスト用依存注入対応）
WebServer     → Qt6 バックグラウンドスレッドから uvicorn を起動・停止
ConnectionManager → WebSocket 接続管理・ブロードキャスト
generate_qr_png   → アクセス URL の QR コード PNG 生成
"""
