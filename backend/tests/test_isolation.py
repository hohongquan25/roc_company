"""Một văn phòng duy nhất: tạo an toàn khi gọi lại, không thể có văn phòng thứ hai; nhân sự cố định dùng chung."""
import sqlite3
import time

import pytest

from mini import db
from mini.orchestrator import queue, teams


def test_workspace_idempotent_and_single(env):
    t1 = teams.ensure_workspace()
    t2 = teams.ensure_workspace()
    assert t1["id"] == t2["id"] == 1
    assert db.q1("SELECT COUNT(*) n FROM teams")["n"] == 1
    # công ty luôn có đúng 5 vai + trợ lý điều hành
    assert db.q1("SELECT COUNT(*) n FROM employees")["n"] == 6
    # lược đồ không cho phép văn phòng thứ hai
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO teams(id, name, slug, workspace_dir, created_at) VALUES(2,'x','x','x',?)", time.time())


def test_path_outside_workspace_rejected(env):
    a = env.team()
    with pytest.raises(PermissionError):
        teams.safe_path_in(teams.team_dir(a), teams.team_dir(a) / ".." / ".." / "ngoai" / "x")


def test_shared_staff_one_job_at_a_time(env):
    """Mỗi nhân viên chỉ làm một việc một lúc; vai khác vẫn nhận việc."""
    a = env.team()
    j1 = queue.enqueue("lead_plan", team_id=a["id"], role="lead", idempotency_key="p1")
    j2 = queue.enqueue("lead_plan", team_id=a["id"], role="lead", idempotency_key="p2")
    jw = queue.enqueue("report", role="exec_assistant", idempotency_key="rp")
    first = queue.claim("w", 120)
    assert first["id"] == j1
    # Minh (Trưởng nhóm) đang bận → việc thứ hai của Minh chờ; trợ lý vẫn nhận được việc
    second = queue.claim("w", 120)
    assert second["id"] == jw
    assert queue.claim("w", 120) is None
    queue.complete(first, "w", {})
    assert queue.claim("w", 120)["id"] == j2


def test_rename_employee_and_list_staff(env):
    from fastapi.testclient import TestClient
    from mini.api.app import app
    env.team()
    c = TestClient(app, headers={"X-Mini": "1"})
    staff = c.get("/api/employees").json()
    assert [e["name"] for e in staff][:3] == ["Minh", "Lan", "Hùng"] and len(staff) == 6
    r = c.patch("/api/employees/NV-WRITER", json={"name": "  Hùng   Nguyễn "})
    assert r.status_code == 200 and r.json()["name"] == "Hùng Nguyễn"
    assert c.patch("/api/employees/NV-WRITER", json={"name": "   "}).status_code == 400
