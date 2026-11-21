"""Điểm vào dòng lệnh: python -m mini {serve|worker|migrate|health}."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    p = argparse.ArgumentParser(prog="mini")
    p.add_argument("command", choices=["serve", "worker", "migrate", "health"])
    a = p.parse_args()
    from . import db
    from .config import get_settings

    if a.command == "migrate":
        print(f"schema version {db.migrate()}")
    elif a.command == "serve":
        import uvicorn
        db.migrate()
        s = get_settings()
        uvicorn.run("mini.api.app:app", host=s.host, port=s.port, log_level="warning")
    elif a.command == "worker":
        from .orchestrator.worker import AlreadyRunning, main as wmain
        try:
            wmain()
        except AlreadyRunning as e:
            print(e, file=sys.stderr)
            sys.exit(3)
    elif a.command == "health":
        db.migrate()
        from .orchestrator.ai import cli_health
        from .media.ff import available
        print(json.dumps({"cli": cli_health(), "ffmpeg": available()}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
