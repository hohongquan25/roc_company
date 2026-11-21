"""API (bảo vệ localhost/CSRF, văn phòng duy nhất, văn phòng 2D) và media với file thật."""
import time

from fastapi.testclient import TestClient

from mini import db
from mini.media import analysis, ff, render, verify
from helpers import make_tone, new_request


def client():
    from mini.api.app import app
    return TestClient(app)


def test_write_requires_header_and_local_host(env):
    c = client()
    assert c.post("/api/company/start").status_code == 403
    assert c.post("/api/company/start", headers={"X-Mini": "1"}).json()["company_running"] is True
    assert c.get("/api/overview", headers={"host": "evil.example.com"}).status_code == 403
    assert c.post("/api/requests", json={"genre": "lofi"}).status_code == 403


def test_single_workspace_and_pause(env):
    from mini.orchestrator import teams
    c = client()
    w1 = c.get("/api/workspace").json()
    w2 = c.get("/api/workspace").json()
    assert w1["id"] == w2["id"] == teams.ensure_workspace()["id"]
    assert len(c.get("/api/teams").json()) == 1
    assert c.patch("/api/workspace", json={"paused": True}, headers={"X-Mini": "1"}).json()["paused"] is True
    # không còn cài đặt nội dung mặc định cho văn phòng
    assert not {"strategy", "media", "template", "mode"} & set(w1)


def test_create_request_via_api_and_list(env):
    c = client()
    h = {"X-Mini": "1"}
    bad = c.post("/api/requests", json={"genre": "lofi", "links": "javascript:alert(1)"}, headers=h)
    assert bad.status_code == 400
    r = c.post("/api/requests", json={"genre": "ballad buồn", "links": "https://example.com/a\n\nhttps://example.com/b",
                                      "output_type": "long", "track_count": 4, "vocals": "vocal"}, headers=h).json()
    assert r["links"] == ["https://example.com/a", "https://example.com/b"] and r["stage"] == "lead"
    lst = c.get("/api/requests").json()
    assert lst["counts"]["open"] == 1 and lst["items"][0]["id"] == r["id"]
    d = c.get(f"/api/requests/{r['id']}").json()
    assert d["reports"][0]["author"] == "Bạn" and "ballad buồn" in d["reports"][0]["body"]
    assert c.get("/api/requests/999").status_code == 404


def test_analysis_measures_tone(env):
    p = make_tone(env.tmp / "t.wav", sec=10, bpm=120)
    m = analysis.analyze_file(p, env.tmp / "w")
    assert abs(m["tempo_bpm"] - 120) < 6 and m["key"]["label"] == "A major"
    assert abs(m["duration_sec"] - 10) < 0.2 and -30 < m["loudness_lufs"] < 0
    assert p.exists()  # analyze_file không xóa; measure_and_forget mới xóa
    analysis.measure_and_forget(p, env.tmp / "w2")
    assert not p.exists()
    assert "BPM" in analysis.describe(m)


def test_normalize_hits_target(env):
    quiet = make_tone(env.tmp / "q.wav", sec=6, volume=0.05)
    res = render.normalize(quiet, env.tmp / "n.wav", -14.0)
    assert res["before"]["i"] < -20 and abs(res["after"]["i"] + 14) < 0.6


def test_verify_detects_broken_and_silent_file(env):
    p = env.tmp / "broken.mp4"
    p.write_bytes(b"\x00" * 20000)
    try:
        rep = verify.verify_output(p, expected_duration=5, width=640, height=360, target_lufs=-14)
        assert not rep["ok"]
    except ff.FFmpegError:
        pass  # ffprobe từ chối file hỏng cũng là phát hiện đúng
    silent = env.tmp / "silent.mp4"
    ff.run(["-f", "lavfi", "-i", "color=c=black:s=640x360:d=6", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "6", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(silent)])
    rep = verify.verify_output(silent, expected_duration=6, width=640, height=360, target_lufs=-14)
    assert not rep["ok"] and not next(c for c in rep["checks"] if c["name"] == "silence")["ok"]


def test_events_redact_tokens(env):
    from mini.events import emit
    emit("x", "lỗi: access_token=ya29.SECRETSECRET và Bearer abc.def")
    m = db.q1("SELECT message FROM events ORDER BY id DESC LIMIT 1")["message"]
    assert "SECRETSECRET" not in m and "abc.def" not in m


def test_office_reflects_real_state(env):
    """Văn phòng 2D chỉ phản ánh trạng thái trong DB: rảnh, đang chạy CLI, chờ lượt, máy đang chạy, cổng chờ bạn."""
    t = env.team()
    tid = t["id"]
    c = client()
    d = c.get("/api/office").json()
    emps = {e["role"]: e for e in d["staff"]}
    assert len(emps) == 6 and all(e["status"] == "idle" and e["job"] is None for e in emps.values())
    assert emps["lead"]["name"] == "Minh" and d["pending_approvals"] == 0
    r = new_request(env)
    jid = db.q1("SELECT id FROM jobs WHERE request_id=?", r["id"])["id"]
    now = time.time()
    db.execute("UPDATE jobs SET status='running' WHERE id=?", jid)
    db.execute("INSERT INTO events(ts, team_id, job_id, level, type, message, data) VALUES(?,?,?,?,?,?,?)",
               now, tid, jid, "info", "cli.tool", "x", '{"name": "WebSearch"}')
    db.execute("UPDATE employees SET status='working', current_job_id=? WHERE id=?", jid, "NV-LEAD")
    db.execute("INSERT INTO jobs(team_id, kind, role, status, payload, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
               tid, "produce", "production", "running", "{}", now, now)
    db.execute("INSERT INTO jobs(team_id, kind, role, status, payload, next_run_at, created_at, updated_at) "
               "VALUES(?,?,?,?,?,?,?,?)", tid, "check", "qa", "awaiting_quota", "{}", now + 60, now, now)
    db.execute("UPDATE requests SET stage='awaiting_uploads', status='gate' WHERE id=?", r["id"])
    d = c.get("/api/office").json()
    emps = {e["role"]: e for e in d["staff"]}
    assert emps["lead"]["job"]["kind"] == "lead_plan" and emps["lead"]["job"]["last_tool"]["name"] == "WebSearch"
    assert emps["lead"]["job"]["request_title"] == r["title"]
    assert d["machines"]["render"]["kind"] == "produce"
    assert emps["qa"]["jobs"]["awaiting_quota"][0]["kind"] == "check"
    assert emps["writer"]["job"] is None and not emps["writer"]["jobs"]
    assert d["pending_approvals"] == 1 and [x["id"] for x in c.get("/api/inbox").json()] == [r["id"]]
    detail = c.get("/api/office/employee/NV-LEAD").json()
    assert detail["employee"]["role_vi"] == "Trưởng nhóm"
