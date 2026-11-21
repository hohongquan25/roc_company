from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent / "fakes"))

from mini import db  # noqa: E402
from mini.adapters import antigravity_cli  # noqa: E402
from mini.config import Settings, set_settings  # noqa: E402
from mini.orchestrator import ai, memory, teams  # noqa: E402

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture
def env(tmp_path, monkeypatch):
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.cli_paths = {"claude": str(FAKES / "fake_claude.py"), "antigravity": str(FAKES / "fake_antigravity.py")}
    s.cli_timeout_sec = 30
    s.quota_wait_sec = 1800
    for d in (s.data_dir, s.teams_dir, s.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    set_settings(s)
    ai.reset_limits()
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"claude": [], "antigravity": []}))
    monkeypatch.setenv("FAKE_CLI_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "cli_log.jsonl"))
    # không đọc file cài đặt Antigravity thật của máy trong kiểm thử
    monkeypatch.setattr(antigravity_cli, "SETTINGS_PATH", tmp_path / "agy-settings.json")
    db.migrate()
    memory.ensure_company_defaults()
    teams.ensure_staff()
    yield Env(s, script, tmp_path)


class Env:
    def __init__(self, s, script, tmp):
        self.s, self.script, self.tmp = s, script, tmp

    def queue_cli(self, flavor: str, *steps: dict) -> None:
        d = json.loads(self.script.read_text())
        d[flavor] += list(steps)
        self.script.write_text(json.dumps(d))

    def cli_log(self) -> list[dict]:
        p = self.tmp / "cli_log.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def team(self) -> dict:
        """Văn phòng duy nhất của ROC (tạo nếu chưa có)."""
        return teams.ensure_workspace()
