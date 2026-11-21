"""HTTP API + SSE + phục vụ giao diện. Chỉ lắng nghe localhost.

Chống CSRF/DNS-rebinding: kiểm tra Host là localhost và mọi yêu cầu ghi phải có
header X-Mini (trang web lạ không gửi được header này mà không qua preflight CORS).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .. import db
from ..config import get_settings
from ..events import emit
from ..media import ff
from ..orchestrator import ai, memory, pipeline, queue, reqs, roles, teams


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.migrate()
    memory.ensure_company_defaults()
    teams.ensure_staff()
    teams.ensure_workspace()
    yield


app = FastAPI(title="ROC", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
MAX_UPLOAD = 300 * 1024 * 1024


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "Host không được phép"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        if request.headers.get("x-mini") != "1":
            return JSONResponse({"detail": "Thiếu header X-Mini"}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(KeyError)
async def key_error(_: Request, e: KeyError):
    return JSONResponse({"detail": str(e).strip("'\"")}, status_code=404)


@app.exception_handler(PermissionError)
async def perm_error(_: Request, e: PermissionError):
    return JSONResponse({"detail": str(e)}, status_code=403)


def _role_vi(role: str) -> str:
    return roles.ROLES.get(role, "Trợ lý điều hành" if role == roles.EXEC_ROLE else role)


def _gates_count() -> int:
    return db.q1("SELECT COUNT(*) n FROM requests WHERE status='gate'")["n"]


# --- tổng quan ----------------------------------------------------------------

_health_cache: dict[str, Any] = {"ts": 0, "data": None}


def _office_row(t: dict) -> dict:
    return {
        "id": t["id"], "name": t["name"], "paused": bool(t["paused"]),
        "requests_active": db.q1("SELECT COUNT(*) n FROM requests WHERE status IN ('active','gate')")["n"],
        "requests_total": db.q1("SELECT COUNT(*) n FROM requests")["n"],
        "waiting_for_you": _gates_count(),
        "open_jobs": db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running','awaiting_approval',"
                           "'awaiting_connection','awaiting_quota','needs_reconcile')")["n"],
    }


@app.get("/api/overview")
def overview():
    t = teams.ensure_workspace()
    return {
        "company": db.company_settings(),
        "jobs": queue.counts(),
        "status_vi": queue.STATUS_VI,
        "office": {**_office_row(t), "working": db.qa(
            "SELECT e.id, e.name, e.role, e.cli, e.status, j.kind FROM employees e JOIN jobs j "
            "ON j.id=e.current_job_id WHERE e.status!='idle'")},
        "workers": queue.live_workers(),
        "providers": db.qa("SELECT * FROM provider_state"),
        "pending_approvals": _gates_count(),
        "needs_reconcile": db.q1("SELECT COUNT(*) n FROM jobs WHERE status='needs_reconcile'")["n"],
    }


# --- văn phòng 2D: chỉ phản ánh trạng thái thật trong cơ sở dữ liệu ----------------

_OPEN_JOBS_SQL = ("SELECT j.*, r.title request_title FROM jobs j LEFT JOIN requests r ON r.id=j.request_id ")


def _job_brief(j: dict) -> dict:
    return {"id": j["id"], "kind": j["kind"], "kind_vi": pipeline.JOB_VI.get(j["kind"], j["kind"]),
            "status": j["status"], "request_id": j["request_id"], "request_title": j.get("request_title"),
            "since": j["updated_at"], "next_run_at": j["next_run_at"], "reason": j.get("wait_reason") or j.get("error")}


def _office_employee(e: dict, open_jobs: list[dict]) -> dict:
    cur = None
    if e["current_job_id"]:
        j = db.q1(_OPEN_JOBS_SQL + "WHERE j.id=?", e["current_job_id"])
        if j:
            cur = _job_brief(j)
            run = db.q1("SELECT started_at, cli, observed_model FROM cli_runs WHERE job_id=? AND status='running' "
                        "ORDER BY id DESC LIMIT 1", j["id"])
            if run:
                cur["since"] = run["started_at"]
                cur["model"] = run["observed_model"]
            tool = db.q1("SELECT ts, data FROM events WHERE job_id=? AND type='cli.tool' ORDER BY id DESC LIMIT 1", j["id"])
            if tool:
                cur["last_tool"] = {"name": (db.jload(tool["data"], {}) or {}).get("name"), "ts": tool["ts"]}
    mine = [j for j in open_jobs if pipeline.ROLE_OF.get(j["kind"]) == e["role"]]
    by_status: dict[str, list] = {}
    for j in mine:
        by_status.setdefault(j["status"], []).append(_job_brief(j))
    return {
        "id": e["id"], "role": e["role"], "role_vi": _role_vi(e["role"]),
        "name": e["name"], "cli": e["cli"], "model": e["model"], "status": e["status"], "job": cur,
        "jobs": by_status,
    }


@app.get("/api/office")
def office():
    t = teams.ensure_workspace()
    open_jobs = db.qa(_OPEN_JOBS_SQL + "WHERE j.status IN ('queued','running','awaiting_approval','awaiting_connection',"
                      "'awaiting_quota','needs_reconcile') ORDER BY j.id")
    recent_failed = db.qa(_OPEN_JOBS_SQL + "WHERE j.status='failed' AND j.updated_at>? ORDER BY j.id DESC", db.now() - 86400)
    staff = [_office_employee(e, open_jobs) for e in db.qa("SELECT * FROM employees ORDER BY rowid")]
    for e in staff:
        e["failed"] = [_job_brief(j) for j in recent_failed if pipeline.ROLE_OF.get(j["kind"]) == e["role"]][:3]
    machines = {}
    for j in open_jobs:
        m = pipeline.MACHINE_OF.get(j["kind"])
        if m and j["status"] == "running":
            machines[m] = _job_brief(j)
    lt = time.localtime()
    day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    counts: dict[str, int] = {}
    for j in open_jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    return {
        "company": db.company_settings(),
        "job_counts": counts,
        "done_today": db.q1("SELECT COUNT(*) n FROM jobs WHERE status='done' AND updated_at>=?", day_start)["n"],
        "failed_24h": len(recent_failed),
        "workers_alive": bool(queue.live_workers()),
        "providers": [p for p in db.qa("SELECT * FROM provider_state") if (p["blocked_until"] or 0) > db.now()],
        "pending_approvals": _gates_count(),
        "needs_reconcile": db.q1("SELECT COUNT(*) n FROM jobs WHERE status='needs_reconcile'")["n"],
        "staff": staff,
        "machines": machines,
        "requests_active": db.q1("SELECT COUNT(*) n FROM requests WHERE status IN ('active','gate')")["n"],
        "teams": [_office_row(t)],
        "now": db.now(),
    }


@app.get("/api/office/employee/{emp_id}")
def office_employee(emp_id: str):
    e = db.q1("SELECT * FROM employees WHERE id=?", emp_id)
    if not e:
        raise HTTPException(404)
    kinds = [k for k, r in pipeline.ROLE_OF.items() if r == e["role"]]
    q = ("SELECT * FROM events WHERE job_id IN (SELECT id FROM jobs WHERE kind IN (%s)) "
         "ORDER BY id DESC LIMIT 25") % ",".join("?" * len(kinds))
    return {
        "employee": {**e, "allowed_tools": db.jload(e["allowed_tools"], []), "io_spec": db.jload(e["io_spec"], {}),
                     "role_vi": _role_vi(e["role"])},
        "runs": db.qa("SELECT id, job_id, cli, status, error_kind, observed_model, started_at, ended_at FROM cli_runs "
                      "WHERE employee_id=? ORDER BY id DESC LIMIT 8", emp_id),
        "events": db.qa(q, *kinds) if kinds else [],
        "team_role_kinds": kinds,
    }


@app.get("/api/health")
def health(refresh: bool = False):
    if refresh or not _health_cache["data"] or time.time() - _health_cache["ts"] > 120:
        s = get_settings()
        try:
            import yt_dlp
            ytdlp = {"installed": True, "version": yt_dlp.version.__version__}
        except ImportError as e:
            ytdlp = {"installed": False, "error": str(e)}
        _health_cache["data"] = {
            "cli": ai.cli_health(),
            "ffmpeg": ff.available(),
            "ytdlp": ytdlp,
            "data_dir": str(s.data_dir),
            "limits": {"max_concurrent_cli": s.max_concurrent_cli, "per_cli": s.max_concurrent_per_cli,
                       "timeout_sec": s.cli_timeout_sec, "max_attempts": s.max_attempts, "max_revisions": s.max_revisions,
                       "video": f"{s.video_width}x{s.video_height}@{s.video_fps}", "target_lufs": s.target_lufs},
        }
        _health_cache["ts"] = time.time()
    return _health_cache["data"]


@app.post("/api/cli/{cli}/probe")
def probe_cli(cli: str):
    """Chạy một nhiệm vụ thật rất nhỏ để xác minh CLI (tốn một lượt nhỏ)."""
    from ..adapters.cli_base import CliRequest
    a = ai.get_adapter(cli)
    tmp = get_settings().data_dir / "probe" / cli
    r = a.run(CliRequest(prompt='Trả về đúng JSON: {"ok": true, "model_note": "<tên model bạn đang dùng nếu biết>"}',
                         cwd=tmp, log_dir=tmp / "logs", json_schema={"type": "object", "required": ["ok"]},
                         timeout_sec=180))
    ai.record_probe(cli, r)
    emit("cli.probe", f"Chạy thử {ai.CLI_LABEL[cli]}: {r.kind}", level="info" if r.ok else "warn",
         data={"model": r.observed_model, "session": r.session_id})
    _health_cache["ts"] = 0
    return {"ok": r.ok, "kind": r.kind, "observed_model": r.observed_model, "session_id": r.session_id,
            "data": r.data, "message": r.message[:500]}


@app.post("/api/company/{action}")
def company_action(action: str):
    if action == "start":
        db.set_setting("company_running", True)
        emit("company.start", "Bắt đầu công ty")
    elif action == "pause":
        db.set_setting("company_running", False)
        emit("company.pause", "Tạm dừng công ty (việc đang chạy sẽ làm nốt; không nhận việc mới)")
    elif action == "emergency_stop":
        db.set_setting("company_running", False)
        n = 0
        for j in db.qa("SELECT id FROM jobs WHERE status='running'"):
            queue.cancel(j["id"])
            n += 1
        emit("company.stop", f"Dừng khẩn cấp: yêu cầu hủy {n} việc đang chạy", level="warn")
    else:
        raise HTTPException(404)
    return db.company_settings()


@app.post("/api/company/flags")
def company_flags(flags: dict = Body(...)):
    for k in ("allow_cli_fallback",):
        if k in flags:
            db.set_setting(k, bool(flags[k]))
            emit("company.flag", f"{k} = {bool(flags[k])}")
    return db.company_settings()


# --- văn phòng & nhân viên ----------------------------------------------------


@app.get("/api/meta")
def meta():
    return {"roles": roles.ROLES, "default_cli": roles.DEFAULT_CLI, "stages": reqs.STAGE_VI, "gates": reqs.GATES,
            "jobs": pipeline.JOB_VI, "status_vi": queue.STATUS_VI, "output_types": reqs.OUTPUT_VI,
            "vocals": reqs.VOCALS_VI, "style_max": roles.STYLE_MAX}


@app.get("/api/teams")
def list_teams():
    return [teams.team_json(t) for t in db.qa("SELECT * FROM teams ORDER BY id")]


@app.get("/api/workspace")
def workspace():
    """Văn phòng làm việc duy nhất của ROC."""
    return teams.team_json(teams.ensure_workspace())


@app.patch("/api/workspace")
def patch_workspace(body: dict = Body(...)):
    t = teams.ensure_workspace()
    if "paused" in body:
        db.execute("UPDATE teams SET paused=? WHERE id=?", int(bool(body["paused"])), t["id"])
        emit("team.pause" if body["paused"] else "team.resume",
             "Tạm dừng văn phòng" if body["paused"] else "Tiếp tục văn phòng", team_id=t["id"])
    return workspace()


@app.get("/api/employees")
def list_employees():
    """Nhân sự cố định của công ty (dùng chung cả văn phòng)."""
    teams.ensure_staff()
    emps = db.qa("SELECT e.*, j.kind job_kind, j.request_id job_request FROM employees e "
                 "LEFT JOIN jobs j ON j.id=e.current_job_id ORDER BY e.rowid")
    for e in emps:
        e["allowed_tools"] = db.jload(e["allowed_tools"], [])
        e["io_spec"] = db.jload(e["io_spec"], {})
        e["role_vi"] = _role_vi(e["role"])
        e["job_kind_vi"] = pipeline.JOB_VI.get(e["job_kind"], e["job_kind"]) if e["job_kind"] else None
        e["last_runs"] = db.qa("SELECT id, cli, status, error_kind, observed_model, started_at, ended_at FROM cli_runs "
                               "WHERE employee_id=? ORDER BY id DESC LIMIT 5", e["id"])
    return emps


@app.patch("/api/employees/{emp_id}")
def patch_employee(emp_id: str, body: dict = Body(...)):
    e = db.q1("SELECT * FROM employees WHERE id=?", emp_id)
    if not e:
        raise HTTPException(404)
    sets, vals = [], []
    if "name" in body:
        name = " ".join(str(body["name"] or "").split())[:40]
        if not name:
            raise ValueError("Cần tên nhân viên")
        sets.append("name=?"); vals.append(name)
    if "cli" in body:
        if body["cli"] not in ai.ADAPTERS:
            raise ValueError("CLI không hợp lệ")
        sets.append("cli=?"); vals.append(body["cli"])
    if "fallback_cli" in body:
        if body["fallback_cli"] not in (None, "", *ai.ADAPTERS):
            raise ValueError("CLI dự phòng không hợp lệ")
        sets.append("fallback_cli=?"); vals.append(body["fallback_cli"] or None)
    if "model" in body:
        from ..adapters.cli_base import safe_model
        sets.append("model=?"); vals.append(safe_model(body["model"] or None))
    if "instructions" in body:
        sets.append("instructions=?"); vals.append(str(body["instructions"])[:8000])
    if "allowed_tools" in body:
        allowed = [t for t in body["allowed_tools"] if t in ("web_search", "web_fetch", "read")]
        sets.append("allowed_tools=?"); vals.append(db.jdump(allowed))
    if sets:
        db.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?", *vals, emp_id)
        emit("employee.updated", f"Cập nhật nhân viên {e['name']} ({_role_vi(e['role'])})")
    return db.q1("SELECT * FROM employees WHERE id=?", emp_id)


# --- yêu cầu nhạc -----------------------------------------------------------------


def _req_brief(r: dict) -> dict:
    last = db.q1("SELECT author, title, created_at FROM request_reports WHERE request_id=? ORDER BY id DESC LIMIT 1", r["id"])
    return {
        "id": r["id"], "title": r["title"], "genre": r["genre"], "stage": r["stage"],
        "stage_vi": reqs.STAGE_VI.get(r["stage"], r["stage"]), "status": r["status"], "stage_note": r["stage_note"],
        "gate": reqs.GATES.get(r["stage"]), "output_type": r["output_type"], "output_vi": reqs.OUTPUT_VI[r["output_type"]],
        "track_count": r["track_count"], "vocals": r["vocals"], "vocals_vi": reqs.VOCALS_VI[r["vocals"]],
        "links": reqs.jget(r, "links", []), "created_at": r["created_at"], "updated_at": r["updated_at"],
        "last_report": last,
    }


@app.get("/api/requests")
def list_requests(status: str = "open"):
    where = {"open": "status IN ('active','gate')", "gate": "status='gate'", "done": "status='done'",
             "cancelled": "status='cancelled'"}.get(status, "1=1")
    out = [_req_brief(r) for r in db.qa(f"SELECT * FROM requests WHERE {where} ORDER BY updated_at DESC LIMIT 200")]
    counts = {x["status"]: x["n"] for x in db.qa("SELECT status, COUNT(*) n FROM requests GROUP BY status")}
    return {"items": out, "counts": {"open": counts.get("active", 0) + counts.get("gate", 0), "gate": counts.get("gate", 0),
                                     "done": counts.get("done", 0), "cancelled": counts.get("cancelled", 0),
                                     "all": sum(counts.values())}}


@app.post("/api/requests")
def create_request(body: dict = Body(...)):
    return _req_brief(reqs.create(body))


def _file_url(rid: int, name: str, path: str | None) -> str | None:
    if not path:
        return None
    try:
        v = int(Path(path).stat().st_mtime)
    except OSError:
        return None
    return f"/api/requests/{rid}/out/{name}?v={v}"


@app.get("/api/requests/{rid}")
def request_detail(rid: int):
    r = reqs.get(rid)
    pdata = reqs.jget(r, "prompts")
    outs = reqs.jget(r, "outputs")
    if outs:
        for o in outs.get("singles", []):
            o["url"] = _file_url(rid, f"single_{o['idx']}", o["path"])
            o["thumb_url"] = _file_url(rid, f"thumb_{o['idx']}", o["thumb"])
            o["file"] = Path(o["path"]).name
        if outs.get("long"):
            outs["long"]["url"] = _file_url(rid, "long", outs["long"]["path"])
            outs["long"]["thumb_url"] = _file_url(rid, "thumb_long", outs["long"]["thumb"])
            outs["long"]["file"] = Path(outs["long"]["path"]).name
    research = reqs.jget(r, "research")
    jobs = db.qa("SELECT id, kind, status, attempts, max_attempts, error, wait_reason, updated_at FROM jobs "
                 "WHERE request_id=? ORDER BY id", rid)
    for j in jobs:
        j["kind_vi"] = pipeline.JOB_VI.get(j["kind"], j["kind"])
        j["status_vi"] = queue.STATUS_VI.get(j["status"], j["status"])
    reports = db.qa("SELECT id, step, role, author, title, body, job_id, created_at FROM request_reports "
                    "WHERE request_id=? ORDER BY id", rid)
    tr = reqs.tracks(rid)
    for t in tr:
        t["has_audio"] = bool(t["audio_path"])
        t.pop("audio_path", None)
    imgs = [{"id": f["id"], "filename": f["filename"], "url": f"/api/requests/{rid}/images/{f['id']}"}
            for f in reqs.files(rid, "image")]
    failed = next((j for j in reversed(jobs) if j["status"] == "failed"), None)
    return {
        "request": {**_req_brief(r), "note": r["note"], "lyrics_language": r["lyrics_language"],
                    "prompts_version": r["prompts_version"], "revisions": r["revisions"]},
        "prompts": pdata, "research": {k: research.get(k) for k in ("summary", "tempo", "key_mode")} if research else None,
        "tracks": tr, "images": imgs, "outputs": outs, "reports": reports, "jobs": jobs,
        "missing": reqs.missing_for_production(r) if r["stage"] in reqs.UPLOAD_STAGES else [],
        "can_upload": r["stage"] in reqs.UPLOAD_STAGES,
        "retry_job": failed if r["stage"] == "needs_attention" else None,
    }


def _need_stage(r: dict, *stages: str) -> None:
    if r["stage"] not in stages:
        raise ValueError(f"Yêu cầu đang ở bước “{reqs.STAGE_VI.get(r['stage'], r['stage'])}” — không làm được thao tác này")


@app.post("/api/requests/{rid}/video")
def start_video(rid: int):
    """Dựng video là việc riêng, ngoài quy trình viết prompt: mở bước tải nhạc + ảnh nền."""
    r = reqs.get(rid)
    _need_stage(r, "done")
    if not reqs.jget(r, "prompts"):
        raise ValueError("Yêu cầu chưa có bộ prompt")
    reqs.add_report(rid, "video_start", None, "Bạn bắt đầu làm video", "Tạo nhạc trên Suno rồi tải nhạc + ảnh nền lên.")
    reqs.set_stage(rid, "awaiting_uploads")
    return _req_brief(reqs.get(rid))


@app.post("/api/requests/{rid}/prompts")
def prompts_decision(rid: int, body: dict = Body(...)):
    """Bạn duyệt bộ prompt (bài Mai chưa cho đạt) → hoàn thành, hoặc yêu cầu Hùng sửa kèm ghi chú."""
    r = reqs.get(rid)
    _need_stage(r, "awaiting_prompt_approval")
    note = str(body.get("note") or "").strip()[:2000]
    if body.get("action") == "approve":
        reqs.add_report(rid, "approve_prompts", None, f"Bạn duyệt prompt phiên bản {r['prompts_version']}",
                        note or "Duyệt — bộ prompt hoàn thành.")
        from ..orchestrator import pipeline
        pdata = reqs.jget(r, "prompts") or {}
        for i, t in enumerate(pdata.get("tracks") or []):
            if t.get("state") != "ok":
                pipeline.sheet_push(r, pdata["version"], i, t)
        reqs.set_stage(rid, "done")
        return _req_brief(reqs.get(rid))
    if body.get("action") == "revise":
        if not note:
            raise ValueError("Ghi rõ cần sửa gì để Hùng làm theo")
        reqs.add_report(rid, "revise_prompts", None, "Bạn yêu cầu sửa prompt", note)
        db.execute("UPDATE requests SET revisions=0 WHERE id=?", rid)
        reqs.set_stage(rid, "writing", "Sửa theo yêu cầu của bạn")
        reqs.enqueue(r, "write", {"feedback": [f"Yêu cầu của giám đốc: {note}"]}, priority=60)
        return _req_brief(reqs.get(rid))
    raise ValueError("action phải là approve | revise")


async def _save_upload(file: UploadFile, dest: Path) -> Path:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise ValueError("File quá lớn (tối đa 300 MB)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _check_audio(p: Path) -> float:
    try:
        info = ff.probe(p)
    except ff.FFmpegError:
        p.unlink(missing_ok=True)
        raise ValueError("File âm thanh không đọc được")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        p.unlink(missing_ok=True)
        raise ValueError("File không có âm thanh")
    return float(info["format"].get("duration") or 0)


@app.post("/api/requests/{rid}/reference")
async def upload_reference(rid: int, file: UploadFile = File(...)):
    """Bài mẫu không tải được qua link → bạn tải file lên; Lan đo rồi xóa file ngay."""
    r = reqs.get(rid)
    _need_stage(r, "awaiting_reference", "needs_attention")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in reqs.AUDIO_EXT:
        raise ValueError("Định dạng âm thanh không hỗ trợ (mp3, wav, m4a, flac, ogg, aac)")
    dest = await _save_upload(file, reqs.rdir(r) / "reference" / f"ref_{int(time.time() * 1000)}{ext}")
    dur = _check_audio(dest)
    db.execute("INSERT INTO request_files(request_id, kind, path, filename, created_at) VALUES(?,?,?,?,?)",
               rid, "reference_audio", str(dest), file.filename or dest.name, db.now())
    reqs.add_report(rid, "reference", None, "Bạn tải file bài mẫu", f"{file.filename} ({dur:.0f}s) — chỉ dùng để đo, "
                    "sẽ bị xóa sau khi phân tích.")
    reqs.set_stage(rid, "research")
    reqs.enqueue(r, "research", {"skip_links": True}, priority=60)
    return {"ok": True, "duration": dur}


@app.post("/api/requests/{rid}/tracks/{idx}/audio")
async def upload_track(rid: int, idx: int, file: UploadFile = File(...)):
    r = reqs.get(rid)
    _need_stage(r, *reqs.UPLOAD_STAGES)
    t = db.q1("SELECT * FROM tracks WHERE request_id=? AND idx=?", rid, idx)
    if not t:
        raise KeyError("không có bài này")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in reqs.AUDIO_EXT:
        raise ValueError("Định dạng âm thanh không hỗ trợ (mp3, wav, m4a, flac, ogg, aac)")
    dest = await _save_upload(file, reqs.rdir(r) / "tracks" / f"track_{idx:02d}_{int(time.time() * 1000)}{ext}")
    dur = _check_audio(dest)
    if dur < 5:
        dest.unlink(missing_ok=True)
        raise ValueError("Bài nhạc quá ngắn (< 5 giây)")
    if t["audio_path"] and Path(t["audio_path"]) != dest:
        Path(t["audio_path"]).unlink(missing_ok=True)
    db.execute("UPDATE tracks SET audio_path=?, audio_name=?, duration=?, uploaded_at=? WHERE id=?",
               str(dest), (file.filename or dest.name)[:200], dur, db.now(), t["id"])
    emit("request.upload", f"Yêu cầu #{rid}: nhận nhạc bài {idx + 1} ({dur:.0f}s)", team_id=r["team_id"], request_id=rid)
    return {"ok": True, "duration": dur}


@app.get("/api/requests/{rid}/tracks/{idx}/audio")
def get_track_audio(rid: int, idx: int):
    r = reqs.get(rid)
    t = db.q1("SELECT * FROM tracks WHERE request_id=? AND idx=?", rid, idx)
    if not t or not t["audio_path"]:
        raise HTTPException(404)
    return FileResponse(teams.safe_path_in(reqs.rdir(r), Path(t["audio_path"])))


@app.post("/api/requests/{rid}/images")
async def upload_image(rid: int, file: UploadFile = File(...)):
    r = reqs.get(rid)
    _need_stage(r, *reqs.UPLOAD_STAGES)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in reqs.IMAGE_EXT:
        raise ValueError("Định dạng ảnh không hỗ trợ (png, jpg, webp)")
    name = reqs.safe_name(file.filename or "", "anh")
    dest = await _save_upload(file, reqs.rdir(r) / "images" / f"{int(time.time() * 1000)}_{name}{ext}")
    try:
        with Image.open(dest) as im:
            im.verify()
        with Image.open(dest) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 — Pillow ném nhiều loại lỗi khác nhau cho ảnh hỏng
        dest.unlink(missing_ok=True)
        raise ValueError("File ảnh không đọc được")
    fid = db.execute("INSERT INTO request_files(request_id, kind, path, filename, created_at) VALUES(?,?,?,?,?)",
                     rid, "image", str(dest), (file.filename or dest.name)[:200], db.now())
    return {"id": fid, "width": w, "height": h, "warning": "Ảnh nhỏ hơn 1920x1080 sẽ bị phóng to, có thể mờ"
            if w < 1920 or h < 1080 else None}


@app.get("/api/requests/{rid}/images/{fid}")
def get_image(rid: int, fid: int):
    r = reqs.get(rid)
    f = db.q1("SELECT * FROM request_files WHERE id=? AND request_id=? AND kind='image'", fid, rid)
    if not f:
        raise HTTPException(404)
    return FileResponse(teams.safe_path_in(reqs.rdir(r), Path(f["path"])))


@app.delete("/api/requests/{rid}/images/{fid}")
def delete_image(rid: int, fid: int):
    r = reqs.get(rid)
    _need_stage(r, *reqs.UPLOAD_STAGES)
    f = db.q1("SELECT * FROM request_files WHERE id=? AND request_id=? AND kind='image'", fid, rid)
    if f:
        Path(f["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM request_files WHERE id=?", fid)
    return {"deleted": fid}


@app.post("/api/requests/{rid}/produce")
def start_production(rid: int):
    r = reqs.get(rid)
    _need_stage(r, *reqs.UPLOAD_STAGES)
    miss = reqs.missing_for_production(r)
    if miss:
        raise ValueError("Còn thiếu: " + ", ".join(miss))
    n = len(reqs.files(rid, "image"))
    reqs.add_report(rid, "start_production", None, "Bạn bấm Bắt đầu dựng",
                    f"Đã tải đủ {r['track_count']} bài nhạc và {n} ảnh nền.")
    reqs.set_stage(rid, "producing")
    reqs.enqueue(r, "produce", priority=60)
    return _req_brief(reqs.get(rid))


@app.post("/api/requests/{rid}/final")
def final_decision(rid: int, body: dict = Body(...)):
    """Cổng cuối: duyệt video (→ trợ lý tổng kết) hoặc yêu cầu làm lại (dựng lại / viết lại prompt)."""
    r = reqs.get(rid)
    note = str(body.get("note") or "").strip()[:2000]
    action = body.get("action")
    if action == "approve":
        _need_stage(r, "awaiting_final")
        reqs.add_report(rid, "approve_final", None, "Bạn duyệt video", note or "Đạt — sẵn sàng đăng.")
        reqs.set_stage(rid, "summary")
        reqs.enqueue(r, "summary", key="final", priority=60)
        return _req_brief(reqs.get(rid))
    if action == "redo":
        _need_stage(r, "awaiting_final", "needs_attention")
        step = body.get("step")
        if not note:
            raise ValueError("Ghi rõ cần làm lại gì")
        if step == "produce":
            miss = reqs.missing_for_production(r)
            if miss:
                raise ValueError("Còn thiếu: " + ", ".join(miss))
            reqs.add_report(rid, "redo", None, "Bạn yêu cầu dựng lại", note)
            reqs.set_stage(rid, "producing")
            reqs.enqueue(r, "produce", {"feedback": note}, priority=60)
        elif step == "write":
            reqs.add_report(rid, "redo", None, "Bạn yêu cầu viết lại prompt", note +
                            "\n\n_Nhạc đã tải lên của bộ prompt cũ sẽ được gỡ khi có bộ prompt mới._")
            db.execute("UPDATE requests SET revisions=0 WHERE id=?", rid)
            reqs.set_stage(rid, "writing", "Viết lại theo yêu cầu của bạn")
            reqs.enqueue(r, "write", {"feedback": [f"Yêu cầu của giám đốc: {note}"]}, priority=60)
        else:
            raise ValueError("step phải là produce | write")
        return _req_brief(reqs.get(rid))
    raise ValueError("action phải là approve | redo")


_STEP_STAGE = {"lead_plan": "lead", "research": "research", "write": "writing", "research_track": "writing", "write_track": "writing", "check_track": "writing", "check": "checking",
               "produce": "producing", "verify": "verifying", "summary": "summary"}


@app.post("/api/requests/{rid}/retry")
def retry_step(rid: int):
    """Bước bị lỗi hẳn → thử lại đúng việc đó."""
    r = reqs.get(rid)
    _need_stage(r, "needs_attention")
    j = db.q1("SELECT * FROM jobs WHERE request_id=? AND status='failed' ORDER BY id DESC LIMIT 1", rid)
    if not j or not queue.retry(j["id"]):
        raise ValueError("Không có bước lỗi nào để thử lại")
    reqs.add_report(rid, "retry", None, f"Bạn cho thử lại: {pipeline.JOB_VI.get(j['kind'], j['kind'])}", "Thử lại bước bị lỗi.")
    reqs.set_stage(rid, _STEP_STAGE.get(j["kind"], r["stage"]))
    return _req_brief(reqs.get(rid))


@app.post("/api/requests/{rid}/cancel")
def cancel_request(rid: int):
    r = reqs.get(rid)
    if r["stage"] in ("done", "cancelled"):
        raise ValueError("Yêu cầu đã kết thúc")
    n = reqs.cancel_open_jobs(rid)
    reqs.add_report(rid, "cancel", None, "Bạn hủy yêu cầu", f"Đã hủy {n} việc đang mở.")
    reqs.set_stage(rid, "cancelled")
    return _req_brief(reqs.get(rid))


@app.get("/api/requests/{rid}/out/{name}")
def output_file(rid: int, name: str):
    r = reqs.get(rid)
    outs = reqs.jget(r, "outputs") or {}
    path = None
    if name == "long" and outs.get("long"):
        path = outs["long"]["path"]
    elif name == "thumb_long" and outs.get("long"):
        path = outs["long"]["thumb"]
    else:
        kind, _, idx = name.partition("_")
        for o in outs.get("singles", []):
            if idx.isdigit() and o["idx"] == int(idx):
                path = {"single": o["path"], "thumb": o["thumb"]}.get(kind)
    if not path:
        raise HTTPException(404)
    p = teams.safe_path_in(reqs.rdir(r), Path(path))
    return FileResponse(p, filename=p.name if p.suffix == ".mp4" else None)


@app.get("/api/inbox")
def inbox():
    """Các yêu cầu đang chờ BẠN (cổng duyệt / tải lên / xử lý sự cố)."""
    return [_req_brief(r) for r in db.qa("SELECT * FROM requests WHERE status='gate' ORDER BY updated_at")]


# --- công việc ----------------------------------------------------------------


@app.get("/api/jobs")
def jobs(status: str | None = None, request_id: int | None = None, limit: int = 200):
    q = ("SELECT j.*, r.title request_title FROM jobs j LEFT JOIN requests r ON r.id=j.request_id WHERE 1=1")
    args: list[Any] = []
    if status:
        q += " AND j.status=?"; args.append(status)
    if request_id:
        q += " AND j.request_id=?"; args.append(request_id)
    q += " ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'needs_reconcile' THEN 1 WHEN 'awaiting_approval' THEN 2 " \
         "WHEN 'queued' THEN 3 ELSE 4 END, j.updated_at DESC LIMIT ?"
    args.append(limit)
    out = db.qa(q, *args)
    for j in out:
        j["kind_vi"] = pipeline.JOB_VI.get(j["kind"], j["kind"])
        j["status_vi"] = queue.STATUS_VI.get(j["status"], j["status"])
    return out


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int):
    j = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
    if not j:
        raise HTTPException(404)
    runs = db.qa("SELECT * FROM cli_runs WHERE job_id=? ORDER BY id", job_id)
    for r in runs:
        r["tool_calls"] = db.jload(r["tool_calls"], [])
        r["args"] = db.jload(r["args"], [])
    return {"job": j, "cli_runs": runs, "handoffs": db.qa("SELECT * FROM handoffs WHERE job_id=?", job_id),
            "events": db.qa("SELECT * FROM events WHERE job_id=? ORDER BY id", job_id)}


@app.get("/api/cli_runs/{run_id}/log/{which}")
def cli_log(run_id: int, which: str):
    r = db.q1("SELECT * FROM cli_runs WHERE id=?", run_id)
    if not r or which not in ("stdout", "stderr", "prompt"):
        raise HTTPException(404)
    p = Path(r["stdout_path"]).parent / "prompt.txt" if which == "prompt" else Path(r[f"{which}_path"] or "")
    if not p.exists():
        raise HTTPException(404)
    from ..events import redact
    return JSONResponse({"text": redact(p.read_text(encoding="utf-8", errors="replace")[-200000:])})


@app.post("/api/jobs/{job_id}/{action}")
def job_action(job_id: int, action: str, body: dict = Body(default={})):
    if action == "cancel":
        return {"result": queue.cancel(job_id)}
    if action == "retry":
        if not queue.retry(job_id):
            raise ValueError("Việc này không thử lại trực tiếp được (đang chạy, đã xong, hoặc cần đối chiếu)")
        j = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
        r = db.q1("SELECT * FROM requests WHERE id=?", j["request_id"]) if j["request_id"] else None
        if r and r["stage"] == "needs_attention":
            reqs.set_stage(r["id"], _STEP_STAGE.get(j["kind"], r["stage"]))
        return {"result": "queued"}
    if action == "reconcile":
        return reconcile(job_id, body)
    raise HTTPException(404)


def reconcile(job_id: int, body: dict) -> dict:
    """Người dùng đối chiếu việc chưa rõ kết quả: not_sent → cho chạy lại một lần; abandon → hủy việc."""
    j = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
    if not j or j["status"] != "needs_reconcile":
        raise ValueError("Việc không ở trạng thái Cần đối chiếu")
    res = body.get("resolution")
    if res == "not_sent":
        db.execute("UPDATE jobs SET status='queued', checkpoint='{}', next_run_at=?, attempts=0, "
                   "wait_reason='đã đối chiếu: cho phép chạy lại' WHERE id=?", db.now(), job_id)
    elif res == "abandon":
        queue.cancel(job_id)
    else:
        raise ValueError("resolution phải là not_sent | abandon")
    emit("job.reconciled", f"Đối chiếu việc #{job_id}: {res}", team_id=j["team_id"], job_id=job_id, level="warn")
    return {"result": res}


# --- bộ nhớ -------------------------------------------------------------------


@app.get("/api/memory")
def get_memory(team_id: int | None = None):
    comp = db.qa("SELECT * FROM memories WHERE scope='company' ORDER BY id")
    team = db.qa("SELECT * FROM memories WHERE scope='team' AND team_id=? ORDER BY id", team_id) if team_id else []
    return {"company": comp, "team": team}


@app.post("/api/memory")
def add_memory(body: dict = Body(...)):
    mid = memory.add(body["scope"], body.get("kind", "rule"), str(body["content"])[:2000], team_id=body.get("team_id"),
                     job_id=body.get("job_id"))
    return db.q1("SELECT * FROM memories WHERE id=?", mid)


@app.patch("/api/memory/{mid}")
def patch_memory(mid: int, body: dict = Body(...)):
    if "content" in body:
        db.execute("UPDATE memories SET content=? WHERE id=?", str(body["content"])[:2000], mid)
    if "active" in body:
        db.execute("UPDATE memories SET active=? WHERE id=?", int(bool(body["active"])), mid)
    return db.q1("SELECT * FROM memories WHERE id=?", mid)


@app.delete("/api/memory/{mid}")
def del_memory(mid: int):
    db.execute("DELETE FROM memories WHERE id=?", mid)
    return {"deleted": mid}


# --- báo cáo & nhật ký --------------------------------------------------------


@app.get("/api/reports")
def reports():
    out = db.qa("SELECT * FROM reports ORDER BY id DESC LIMIT 30")
    for r in out:
        r["content"] = db.jload(r["content"], {})
    return out


@app.post("/api/reports")
def make_report(body: dict = Body(default={})):
    teams.ensure_staff()
    jid = queue.enqueue("report", role=roles.EXEC_ROLE, payload={"days": int(body.get("days", 1))},
                        idempotency_key=f"report:{uuid.uuid4().hex[:8]}", priority=40)
    return {"job_id": jid}


@app.get("/api/events")
def events(after: int = 0, request_id: int | None = None, level: str | None = None, limit: int = 300):
    q, args = "SELECT * FROM events WHERE id>?", [after]
    if request_id:
        q += " AND request_id=?"; args.append(request_id)
    if level:
        q += " AND level=?"; args.append(level)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return db.qa(q, *args)


@app.get("/api/stream")
async def stream(request: Request, after: int = 0):
    async def gen():
        last = after or (db.q1("SELECT MAX(id) m FROM events")["m"] or 0)
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            evs = await asyncio.to_thread(db.qa, "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", last)
            for e in evs:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            if not evs:
                yield ": ping\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# --- giao diện ----------------------------------------------------------------


def mount_frontend() -> None:
    dist = get_settings().frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = dist / path
            if path and f.is_file() and f.resolve().is_relative_to(dist.resolve()):
                return FileResponse(f)
            return FileResponse(dist / "index.html")


mount_frontend()
