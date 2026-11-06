#!/usr/bin/env python3
"""Launch the paper-trading cockpit (single canonical launcher).

Run with:  python scripts/run_cockpit.py [--port 8501]

The underlying command is simply ``streamlit run marketmind/cockpit/app.py``;
this wrapper only resolves paths from the repo root and reports the URL.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "marketmind" / "cockpit" / "app.py"


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the paper-trading cockpit")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit server port")
    args = parser.parse_args()

    if _port_is_open(args.port):
        print(f"Port {args.port} is already serving — open http://localhost:{args.port}")
        print("If it is another Streamlit app, stop it first or pass --port 8502.")
        return 0

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.port", str(args.port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    print(f"Cockpit: http://localhost:{args.port}")
    print("Prerequisite: IB Gateway logged into the paper account on port 4002.")
    return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
