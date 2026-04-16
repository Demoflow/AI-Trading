"""Launch the scalper dashboard."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard.server import app
import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("  SCALPER DASHBOARD")
    print("=" * 60)
    print("  URL:     http://localhost:8888")
    print("  Updates: every 2 seconds via WebSocket")
    print("  Data:    config/paper_scalp.json + live Schwab quotes")
    print("  Stop:    Ctrl+C")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8888, log_level="warning")
