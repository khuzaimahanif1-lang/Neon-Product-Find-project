"""Run from any working directory: backend/.venv/Scripts/python.exe backend/run.py."""

import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import Settings  # noqa: E402

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.port, ws_max_size=4096)
