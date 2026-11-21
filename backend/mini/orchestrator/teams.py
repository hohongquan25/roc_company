"""Văn phòng làm việc duy nhất của ROC (idempotent): không gian làm việc + nhân sự cố định."""
from __future__ import annotations

from pathlib import Path

from .. import db
from ..config import get_settings
from ..events import emit
from . import memory, roles

WORKSPACE_ID = 1
WORKSPACE_TITLE = "Văn phòng ROC"
WORKSPACE_SLUG = "van-phong-roc"


def team_dir(team: dict) -> Path:
    return Path(team["workspace_dir"])


def ensure_staff() -> None:
    """Bảo đảm công ty có đủ nhân sự cố định: mỗi vai một người + trợ lý điều hành."""
    for r in [*roles.ROLES, roles.EXEC_ROLE]:
        if db.q1("SELECT id FROM employees WHERE role=?", r):
            continue
        db.execute(
            "INSERT INTO employees(id, team_id, role, name, cli, model, instructions, allowed_tools, io_spec) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            roles.STAFF_ID[r], None, r, roles.STAFF_NAME[r], roles.DEFAULT_CLI[r], None, roles.INSTRUCTIONS[r],
            db.jdump(roles.DEFAULT_TOOLS[r]), db.jdump(roles.IO_SPEC[r]),
        )


def ensure_workspace() -> dict:
    """Bảo đảm ROC có đúng một văn phòng làm việc (idempotent). Bảng teams chỉ cho phép id=1,
    nên không thể có văn phòng thứ hai. Không có cài đặt nội dung mặc định: mọi việc đi từ yêu cầu của bạn."""
    t = db.q1("SELECT * FROM teams WHERE id=?", WORKSPACE_ID)
    if not t:
        wdir = get_settings().teams_dir / WORKSPACE_SLUG
        with db.tx() as c:
            created = c.execute(
                "INSERT OR IGNORE INTO teams(id, name, slug, paused, workspace_dir, created_at) VALUES(?,?,?,?,?,?)",
                (WORKSPACE_ID, WORKSPACE_TITLE, WORKSPACE_SLUG, 0, str(wdir), db.now()),
            ).rowcount == 1
        if created:
            memory.ensure_company_defaults()
            ensure_staff()
            emit("team.created", f"Mở {WORKSPACE_TITLE}", team_id=WORKSPACE_ID)
        t = db.q1("SELECT * FROM teams WHERE id=?", WORKSPACE_ID)
    for sub in ("requests", "jobs"):
        (team_dir(t) / sub).mkdir(parents=True, exist_ok=True)
    return t


def get_team(team_id: int) -> dict:
    t = db.q1("SELECT * FROM teams WHERE id=?", team_id)
    if not t:
        raise KeyError("không có văn phòng")
    return t


def team_json(t: dict) -> dict:
    return {**t, "paused": bool(t["paused"])}


def safe_path_in(base: Path, p: Path) -> Path:
    """Chặn truy cập ra ngoài không gian làm việc của văn phòng."""
    rp = p.resolve()
    if not rp.is_relative_to(base.resolve()):
        raise PermissionError("đường dẫn nằm ngoài không gian làm việc của văn phòng")
    return rp
