"""Luồng yêu cầu nhạc đầu-cuối (AI = MOCK, đo âm thanh và dựng video = librosa/FFmpeg thật, không cần mạng):
yêu cầu → kế hoạch → nghiên cứu → prompt Suno → kiểm tra (vòng sửa) → bạn duyệt → bạn tải nhạc + ảnh
→ dựng → kiểm tra video → bạn duyệt → tổng kết. Mỗi bước để lại báo cáo trên dòng thời gian."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mini import db
from mini.media import analysis
from mini.orchestrator import pipeline, reqs
from helpers import (BRIEF, CHECK_PASS, CHECK_REVISE, LEAD, RESEARCH, SUMMARY, job_of, make_image, make_tone, new_request,
                     outline_out, run_next, stage, track_out, write_out)

H = {"X-Mini": "1"}


def client():
    from mini.api.app import app
    return TestClient(app)


def titles(rid: int) -> list[str]:
    return [r["title"] for r in db.qa("SELECT title FROM request_reports WHERE request_id=? ORDER BY id", rid)]


def add_reference(env, rid: int, name: str = "mau.wav") -> Path:
    p = make_tone(env.tmp / name, sec=12)
    db.execute("INSERT INTO request_files(request_id, kind, path, filename, created_at) VALUES(?,?,?,?,?)",
               rid, "reference_audio", str(p), name, 0)
    return p


def to_prompts_done(env, rid: int, n: int = 2) -> None:
    """Minh ∥ Lan → mỗi bài: Lan định hướng → Hùng viết → Mai soát đạt → yêu cầu hoàn thành."""
    env.queue_cli("claude", {"type": "ok", "structured": LEAD}, {"type": "ok", "structured": RESEARCH},
                  *[x for i in range(n) for x in ({"type": "ok", "structured": BRIEF},
                                                  {"type": "ok", "structured": track_out(i)})])
    env.queue_cli("antigravity", *[{"type": "ok", "structured": CHECK_PASS} for _ in range(n)])
    for k in ("lead_plan", "research"):
        st, j = run_next(k)
        assert st == "done", (k, j["error"])
    for _ in range(n):
        for k in ("research_track", "write_track", "check_track"):
            st, j = run_next(k)
            assert st == "done", (k, j["error"])
    assert stage(rid) == "done"


def test_request_creation_validation_and_lead_report(env):
    with pytest.raises(ValueError):
        new_request(env, genre=" ")
    with pytest.raises(ValueError):
        new_request(env, links=["ftp://x/y"])
    with pytest.raises(ValueError):
        new_request(env, track_count=0)
    r = new_request(env, links=["https://example.com/bai-mau"])
    assert r["stage"] == "lead" and titles(r["id"]) == ["Bạn gửi yêu cầu"]
    assert job_of("lead_plan", r["id"])["status"] == "queued" and job_of("research", r["id"])["status"] == "queued"
    env.queue_cli("claude", {"type": "ok", "structured": LEAD})
    assert run_next("lead_plan")[0] == "done"
    assert stage(r["id"]) == "lead" and not job_of("research_track", r["id"])  # chờ Lan đo xong
    rep = db.q1("SELECT * FROM request_reports WHERE request_id=? AND step='lead_plan'", r["id"])
    assert rep["author"].startswith("Minh") and "Phân công" in rep["body"] and "Lan" in rep["body"]
    # prompt gửi trưởng nhóm có đủ yêu cầu của bạn
    assert "lofi chill piano" in env.cli_log()[-1]["prompt"] and "https://example.com/bai-mau" in env.cli_log()[-1]["prompt"]


def test_research_measures_reference_then_deletes_audio(env):
    r = new_request(env)
    ref = add_reference(env, r["id"])
    env.queue_cli("claude", {"type": "ok", "structured": LEAD}, {"type": "ok", "structured": RESEARCH})
    run_next("lead_plan")
    st, j = run_next("research")
    assert st == "done", j["error"]
    data = db.jload(reqs.get(r["id"])["research"])
    m = data["references"][0]
    assert abs(m["tempo_bpm"] - 120) < 6 and m["key"]["key"] == "A" and abs(m["duration_sec"] - 12) < 0.2
    assert m["loudness_lufs"] < 0 and m["energy_curve"] and m["sections"]
    # bài mẫu không được giữ lại: file và bản ghi đều đã xóa, chỉ còn con số
    assert not ref.exists() and not reqs.files(r["id"], "reference_audio")
    prompt = env.cli_log()[-1]["prompt"]
    assert "tempo_bpm" in prompt and "energy_curve" not in prompt
    rep = db.q1("SELECT body FROM request_reports WHERE request_id=? AND step='research'", r["id"])["body"]
    assert "BPM" in rep and "A major" in rep and "đã bị xóa" in rep
    assert stage(r["id"]) == "writing" and job_of("research_track", r["id"])["status"] == "queued"


def test_link_download_failure_waits_for_your_file(env, monkeypatch):
    def boom(url, out, mx):
        raise analysis.DownloadError("HTTP Error 403: Forbidden")
    monkeypatch.setattr(analysis, "download_reference", boom)
    r = new_request(env, links=["https://example.com/bai-mau"])
    env.queue_cli("claude", {"type": "ok", "structured": LEAD})
    run_next("lead_plan")
    assert run_next("research")[0] == "done"
    assert stage(r["id"]) == "awaiting_reference" and "Không tải được bài mẫu" in titles(r["id"])
    assert [x["id"] for x in client().get("/api/inbox").json()] == [r["id"]]
    # bạn tải file bài mẫu → Lan đo lại (không thử link nữa) và đi tiếp
    p = make_tone(env.tmp / "up.wav", sec=8)
    c = client()
    res = c.post(f"/api/requests/{r['id']}/reference", files={"file": ("up.wav", p.read_bytes(), "audio/wav")}, headers=H)
    assert res.status_code == 200, res.text
    assert stage(r["id"]) == "research"
    env.queue_cli("claude", {"type": "ok", "structured": RESEARCH})
    st, j = run_next("research")
    assert st == "done", j["error"]
    assert db.jload(j["payload"])["skip_links"] and stage(r["id"]) == "writing"
    assert job_of("research_track", r["id"])["status"] == "queued"


def test_track_pipeline_runs_in_parallel_and_qa_loop(env):
    r = new_request(env)
    rid = r["id"]
    env.queue_cli("claude", {"type": "ok", "structured": LEAD}, {"type": "ok", "structured": RESEARCH},
                  {"type": "ok", "structured": BRIEF}, {"type": "ok", "structured": track_out(0)},
                  {"type": "ok", "structured": BRIEF}, {"type": "ok", "structured": track_out(1)},
                  {"type": "ok", "structured": track_out(1, style=
                      "lofi hip hop, warm rhodes lead instead of piano, brushed drums, deep bass, 72 bpm, rainy night")})
    env.queue_cli("antigravity", {"type": "ok", "structured": CHECK_PASS}, {"type": "ok", "structured": CHECK_REVISE},
                  {"type": "ok", "structured": CHECK_PASS})
    from mini.orchestrator import queue
    # Minh và Lan nhận việc cùng lúc
    a = queue.claim("w1", 60, kinds_blocked={"research"})
    b = queue.claim("w2", 60, kinds_blocked={"lead_plan"})
    assert {a["kind"], b["kind"]} == {"lead_plan", "research"}
    db.execute("UPDATE jobs SET status='queued', lease_owner=NULL WHERE id IN (?,?)", a["id"], b["id"])
    for k in ("lead_plan", "research"):
        assert run_next(k)[0] == "done"
    assert [t["title"] for t in reqs.tracks(rid)] == ["Mưa đêm số 1", "Mưa đêm số 2"]
    assert run_next("research_track")[0] == "done"  # Lan định hướng bài 1 → đẩy Hùng
    # Hùng viết bài 1 trong khi Lan định hướng bài 2
    a = queue.claim("w1", 60, kinds_blocked={"research_track"})
    b = queue.claim("w2", 60, kinds_blocked={"write_track"})
    assert a["kind"] == "write_track" and b["kind"] == "research_track"
    db.execute("UPDATE jobs SET status='queued', lease_owner=NULL WHERE id IN (?,?)", a["id"], b["id"])
    assert run_next("write_track")[0] == "done"
    assert "Piano mộc dẫn dắt" in env.cli_log()[-1]["prompt"]  # định hướng của Lan đến tay Hùng
    assert run_next("research_track")[0] == "done"
    assert run_next("check_track")[0] == "done"  # bài 1 đạt
    assert run_next("write_track")[0] == "done"
    assert run_next("check_track")[0] == "done"  # bài 2 cần sửa
    assert stage(rid) == "writing"
    st, j = run_next("write_track")
    assert st == "done" and db.jload(j["payload"])["idx"] == 1
    assert "đổi nhạc cụ chính" in env.cli_log()[-1]["prompt"]  # góp ý của Mai đến tay Hùng
    assert run_next("check_track")[0] == "done"
    rq = reqs.get(rid)
    assert rq["stage"] == "done" and rq["prompts_version"] == 1  # đạt hết → hoàn thành, không qua video
    p = db.jload(rq["prompts"])
    assert [t["state"] for t in p["tracks"]] == ["ok", "ok"] and "rhodes" in p["tracks"][1]["style"]
    t = titles(rid)
    assert t.index("Kế hoạch & dàn bài") < t.index("Định hướng bài 1 (phiên bản 1)") < \
        t.index("Prompt bài 1 (phiên bản 1)") < t.index("Kiểm tra bài 2 (phiên bản 1): CẦN SỬA") < \
        t.index("Kiểm tra bài 2 (phiên bản 1): ĐẠT") < t.index("Hoàn thành bộ prompt phiên bản 1")


def test_escalated_track_goes_to_you_then_redo(env):
    env.s.max_revisions = 0
    r = new_request(env, track_count=1)
    rid = r["id"]
    lead = {**LEAD, "tracks": LEAD["tracks"][:1]}
    env.queue_cli("claude", {"type": "ok", "structured": lead}, {"type": "ok", "structured": RESEARCH},
                  {"type": "ok", "structured": BRIEF}, {"type": "ok", "structured": track_out(0)})
    env.queue_cli("antigravity", {"type": "ok", "structured": CHECK_REVISE})
    for k in ("lead_plan", "research", "research_track", "write_track", "check_track"):
        assert run_next(k)[0] == "done"
    assert stage(rid) == "awaiting_prompt_approval"
    c = client()
    assert c.post(f"/api/requests/{rid}/prompts", json={"action": "revise"}, headers=H).status_code == 400
    c.post(f"/api/requests/{rid}/prompts", json={"action": "revise", "note": "thêm tiếng mưa"}, headers=H)
    assert stage(rid) == "writing" and "thêm tiếng mưa" in db.jload(job_of("write", rid)["payload"])["feedback"][0]


def test_sheet_push_after_qa_pass(env, monkeypatch):
    import urllib.request
    sent = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b'{"ok": true}'

    def fake_open(req, timeout=0):
        import json as _j
        sent.append(_j.loads(req.data.decode("utf-8")))
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    env.s.sheets_webhook_url = "https://script.google.com/macros/s/x/exec"
    r = new_request(env)
    to_prompts_done(env, r["id"])
    for _ in range(2):
        assert run_next("sheet_push")[0] == "done"
    assert [x["track_no"] for x in sent] == [1, 2] and sent[0]["style"].startswith("lofi hip hop")
    assert sent[0]["request_id"] == r["id"] and sent[0]["title"] == "Mưa đêm số 1"


def test_wrong_track_count_is_rejected(env):
    r = new_request(env, track_count=3)
    env.queue_cli("claude", {"type": "ok", "structured": LEAD})
    st, j = run_next("lead_plan")
    assert st == "queued" and j["error_kind"] == "invalid_output" and "3 bài" in j["error"]


def test_deterministic_check_catches_reference_names(env):
    r = new_request(env, vocals="vocal")
    db.execute("UPDATE requests SET research=? WHERE id=?", db.jdump(
        {"references": [{"meta": {"uploader": "Nujabes Official", "title": "Aruarian Dance (Official Audio)"}}]}), r["id"])
    rq = reqs.get(r["id"])
    assert "Nujabes" in reqs.names_to_avoid(rq) and "Aruarian Dance" in reqs.names_to_avoid(rq)
    bad = write_out(1, style="jazzy hip hop in the style of Nujabes, mellow piano, 85 bpm, dusty drums", lyrics="")
    issues = pipeline.deterministic_issues(rq, bad)
    reasons = " ".join(i["reason"] for i in issues)
    assert "Nujabes" in reasons and "chưa có lời" in reasons
    long_style = write_out(1, style="x" * 1001, lyrics="[Verse]\nlời mới hoàn toàn của bài hát này")
    assert any("1001 ký tự" in i["reason"] for i in pipeline.deterministic_issues(rq, long_style))


def test_full_flow_upload_gate_render_verify_and_summary(env):
    env.s.video_width, env.s.video_height, env.s.video_fps = 640, 360, 10
    r = new_request(env)
    rid = r["id"]
    to_prompts_done(env, rid)
    c = client()
    assert c.post(f"/api/requests/{rid}/produce", headers=H).status_code == 400  # chưa bấm Dựng video
    assert c.post(f"/api/requests/{rid}/video", headers=H).status_code == 200
    assert stage(rid) == "awaiting_uploads"
    res = c.post(f"/api/requests/{rid}/produce", headers=H)
    assert res.status_code == 400 and "bài 1" in res.json()["detail"] and "ảnh nền" in res.json()["detail"]
    # bạn tạo nhạc trên Suno rồi tải lên (ở đây: âm thử bằng FFmpeg)
    for i, (sec, f) in enumerate(((6, 440), (5, 330))):
        p = make_tone(env.tmp / f"suno_{i}.wav", sec=sec, freq=f)
        res = c.post(f"/api/requests/{rid}/tracks/{i}/audio", files={"file": (f"suno_{i}.wav", p.read_bytes(), "audio/wav")}, headers=H)
        assert res.status_code == 200, res.text
    bad = c.post(f"/api/requests/{rid}/images", files={"file": ("x.png", b"not an image", "image/png")}, headers=H)
    assert bad.status_code == 400
    img = make_image(env.tmp / "nen.png")
    res = c.post(f"/api/requests/{rid}/images", files={"file": ("nen.png", img.read_bytes(), "image/png")}, headers=H)
    assert res.status_code == 200 and res.json()["warning"]  # ảnh nhỏ → cảnh báo mờ
    assert c.get(f"/api/requests/{rid}").json()["missing"] == []
    assert c.post(f"/api/requests/{rid}/produce", headers=H).status_code == 200
    st, j = run_next("produce")
    assert st == "done", j["error"]
    outs = db.jload(reqs.get(rid)["outputs"])
    assert len(outs["singles"]) == 2 and outs["long"] and all(Path(o["path"]).exists() for o in outs["singles"])
    assert outs["long"]["tracklist"] == "00:00 Mưa đêm số 1\n00:06 Mưa đêm số 2"
    assert "Danh sách bài:" in outs["long"]["description"] and abs(outs["long"]["duration"] - 11) < 0.2
    assert all(abs(v["after"] + 14) < 0.6 for v in outs["loudness"].values())
    assert stage(rid) == "verifying"
    st, j = run_next("verify")
    assert st == "done", j["error"]
    assert stage(rid) == "awaiting_final"
    assert db.jload(reqs.get(rid)["outputs"])["verify"]["ok"]
    d = c.get(f"/api/requests/{rid}").json()
    assert c.get(d["outputs"]["long"]["url"]).status_code == 200
    assert c.get(d["outputs"]["singles"][1]["thumb_url"]).status_code == 200
    # bạn duyệt → trợ lý tổng kết → hoàn thành
    assert c.post(f"/api/requests/{rid}/final", json={"action": "approve"}, headers=H).status_code == 200
    env.queue_cli("claude", {"type": "ok", "structured": SUMMARY})
    assert run_next("summary")[0] == "done"
    assert stage(rid) == "done"
    t = titles(rid)
    for x in ("Bạn gửi yêu cầu", "Kế hoạch & dàn bài", "Báo cáo nghiên cứu phong cách", "Định hướng bài 1 (phiên bản 1)",
              "Kiểm tra bài 1 (phiên bản 1): ĐẠT", "Hoàn thành bộ prompt phiên bản 1", "Bạn bắt đầu làm video", "Bạn bấm Bắt đầu dựng",
              "Dựng video xong", "Kiểm tra video: ĐẠT", "Bạn duyệt video", "Hoàn thành 2 bài lofi"):
        assert x in t, x
    reps = db.qa("SELECT author FROM request_reports WHERE request_id=? ORDER BY id", rid)
    assert {x["author"].split(" · ")[0] for x in reps} >= {"Bạn", "Minh", "Lan", "Hùng", "Mai", "Tuấn", "Thảo"}


def test_failed_step_needs_attention_and_retry(env):
    r = new_request(env)
    db.execute("UPDATE employees SET cli='antigravity' WHERE role='lead'")
    db.execute("UPDATE jobs SET max_attempts=1 WHERE request_id=?", r["id"])
    env.queue_cli("antigravity", {"type": "text", "text": "không phải json"}, {"type": "text", "text": "vẫn không"})
    st, _ = run_next("lead_plan")
    assert st == "failed" and stage(r["id"]) == "needs_attention"
    assert "Lập kế hoạch & phân công thất bại" in titles(r["id"])
    c = client()
    assert c.post(f"/api/requests/{r['id']}/retry", headers=H).status_code == 200
    assert stage(r["id"]) == "lead" and job_of("lead_plan", r["id"])["status"] == "queued"


def test_cancel_request(env):
    r = new_request(env)
    c = client()
    assert c.post(f"/api/requests/{r['id']}/cancel", headers=H).status_code == 200
    assert stage(r["id"]) == "cancelled" and job_of("lead_plan", r["id"])["status"] == "cancelled"
    assert c.get("/api/requests?status=open").json()["items"] == []
