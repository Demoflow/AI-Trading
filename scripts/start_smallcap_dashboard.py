"""Launch the Small Cap Dashboard on port 8889."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smallcap.dashboard.server import app
import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("  SMALL CAP DASHBOARD")
    print("=" * 60)
    print("  URL:     http://localhost:8889")
    print("  Updates: every 2 seconds via WebSocket")
    print("  Data:    config/smallcap_portfolio.json + Schwab quotes")
    print("  Stop:    Ctrl+C")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8889, log_level="warning")
