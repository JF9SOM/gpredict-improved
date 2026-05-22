"""
Web module — FastAPI REST API + WebSocket server.

create_app()      → Create FastAPI app (supports dependency injection for tests)
WebServer         → Start/stop uvicorn from a Qt6 background thread
ConnectionManager → Manage WebSocket connections and broadcast
generate_qr_png   → Generate QR code PNG for the access URL
"""
