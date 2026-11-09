# -*- coding: utf-8 -*-
"""Uvicorn 入口：``data_reflex.main:app``；直接运行本文件会打印启动命令提示。"""

from __future__ import annotations

import sys

from data_reflex.api.app import create_app

app = create_app()


def main() -> None:
    """Emit how to start the API server (T45: no bare stdout print helper)."""
    sys.stdout.write(
        "Start DataReflex with:\n"
        "  uvicorn data_reflex.main:app --reload --host 0.0.0.0 --port 8000\n"
    )


if __name__ == "__main__":
    main()
