"""BossMatch - AI-powered dual-side matching desktop app."""

import logging
import os
from pathlib import Path

import webview
from src.api.bridge import AppAPI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

# Project root: one level up from src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


def main():
    api = AppAPI()
    window = webview.create_window(
        title="BossMatch",
        url=str(_FRONTEND_DIR / "index.html"),
        js_api=api,
        width=1280,
        height=800,
        min_size=(960, 600),
    )
    api._window = window
    webview.start(debug=True)


if __name__ == "__main__":
    main()
