"""Entrypoint.

* Vercel auto-detects the FastAPI `app` exported from this file.
* Locally:  .venv\\Scripts\\python main.py   ->  http://127.0.0.1:8765
  (if 8765 is busy, the next free port is used automatically)
"""
from portfolio.app import app

__all__ = ["app"]


def _free_port(host: str, preferred: int, attempts: int = 20) -> int:
    import socket

    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:  # in use / reserved by Windows (WinError 10013 or 10048)
                continue
        return port
    raise SystemExit(f"No free port between {preferred} and {preferred + attempts - 1}. Set PORT=... and retry.")


if __name__ == "__main__":
    import os

    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    wanted = int(os.environ.get("PORT", "8765"))
    port = _free_port(host, wanted)
    if port != wanted:
        print(f"Port {wanted} is busy (is the site already running in another terminal?) - using {port} instead.")
    print(f"Open http://{host}:{port}   ·   admin: http://{host}:{port}/admin")
    uvicorn.run("main:app", host=host, port=port, reload=os.environ.get("RELOAD", "1") == "1")
