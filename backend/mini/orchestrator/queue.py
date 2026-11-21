"""Hàng đợi công việc lưu trong SQLite với cơ chế lease.

Trạng thái: queued (Chờ thực hiện), running (Đang thực hiện), awaiting_approval (Chờ duyệt),
awaiting_connection (Chờ kết nối), awaiting_quota (Chờ lượt sử dụng), needs_reconcile
(Cần đối chiếu), done (Hoàn thành), failed (Thất bại), cancelled (Đã hủy).
"""
from __future__ import annotations

import os
from typing import Any

from .. import db
from ..events import emit

STATUS_VI = {
    "queued": "Chờ thực hiện", "running": "Đang thực hiện", "awaiting_approval": "Chờ duyệt",
    "awaiting_connection": "Chờ kết nối", "awaiting_quota": "Chờ lượt sử dụng",
    "needs_reconcile": "Cần đối chiếu", "done": "Hoàn thành", "failed": "Thất bại", "cancelled": "Đã hủy",
}
OPEN_STATUSES = ("queued", "running", "awaiting_approval", "awaiting_connection", "awaiting_quota", "needs_reconcile")

# loại việc có tác động ra bên ngoài: không được tự chạy lại khi kết quả chưa rõ (hiện không còn loại nào)
SIDE_EFFECT_KINDS: set[str] = set()


def enqueue(kind: str, *, team_id: int | None = None, request_id: int | None = None, role: str | None = None,
            payload: dict | None = None, priority: int = 100, run_at: float | None = None,
            idempotency_key: str | None = None, max_attempts: int = 3) -> int | None:
    """Thêm việc. Nếu idempotency_key đã tồn tại thì trả id cũ (không tạo trùng)."""
    ts = db.now()
    with db.tx() as c:
        if idempotency_key:
            r = c.execute("SELECT id FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if r:
                return r[0]
        cur = c.execute(
            "INSERT INTO jobs(team_id, request_id, kind, role, status, priority, payload, next_run_at, "
            "idempotency_key, side_effect, max_attempts, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (team_id, request_id, kind, role, "queued", priority, db.jdump(payload or {}), run_at or ts,
             idempotency_key, int(kind in SIDE_EFFECT_KINDS), max_attempts, ts, ts),
        )
        jid = cur.lastrowid
    emit("job.queued", f"Thêm việc {kind}", team_id=team_id, job_id=jid, request_id=request_id)
    return jid


def claim(worker_id: str, lease_sec: int, kinds_blocked: set[str] | None = None) -> dict | None:
    """Nhận một việc đến hạn. Khóa ghi IMMEDIATE + điều kiện status='queued' bảo đảm
    hai worker không bao giờ nhận cùng một việc. Trong CÙNG một yêu cầu, một vai chỉ làm một việc
    tại một thời điểm; các yêu cầu khác nhau chạy song song (giới hạn số lần gọi AI nằm ở ai._sems)."""
    ts = db.now()
    with db.tx() as c:
        r = c.execute(
            """SELECT j.id FROM jobs j LEFT JOIN teams t ON t.id = j.team_id
               WHERE j.status='queued' AND j.next_run_at <= ? AND j.cancel_requested=0
                 AND (j.team_id IS NULL OR t.paused = 0)
                 AND (j.role IS NULL OR NOT EXISTS (
                      SELECT 1 FROM jobs r WHERE r.status='running' AND r.role=j.role
                        AND r.request_id IS j.request_id))
               ORDER BY j.priority, j.next_run_at, j.id LIMIT 20""",
            (ts,),
        ).fetchall()
        for (jid,) in r:
            job = db.row(c, "SELECT * FROM jobs WHERE id=?", jid)
            if kinds_blocked and job["kind"] in kinds_blocked:
                continue
            upd = c.execute(
                "UPDATE jobs SET status='running', lease_owner=?, lease_expires_at=?, attempts=attempts+1, "
                "updated_at=? WHERE id=? AND status='queued'",
                (worker_id, ts + lease_sec, ts, jid),
            )
            if upd.rowcount == 1:
                job["status"], job["lease_owner"], job["attempts"] = "running", worker_id, job["attempts"] + 1
                return job
    return None


def release_employee(employee_id: int, job_id: int) -> None:
    """Nhân viên xong việc job_id. Nếu vai đó còn việc khác đang chạy (yêu cầu khác) thì vẫn 'working'."""
    with db.tx() as c:
        other = c.execute(
            "SELECT j.id FROM jobs j JOIN employees e ON e.role = j.role WHERE e.id=? AND j.status='running' "
            "AND j.id<>? ORDER BY j.id LIMIT 1", (employee_id, job_id)).fetchone()
        if other:
            c.execute("UPDATE employees SET status='working', current_job_id=? WHERE id=?", (other[0], employee_id))
        else:
            c.execute("UPDATE employees SET status='idle', current_job_id=NULL WHERE id=?", (employee_id,))


def heartbeat(job_id: int, worker_id: str, lease_sec: int) -> bool:
    with db.connect() as c:
        r = c.execute("UPDATE jobs SET lease_expires_at=? WHERE id=? AND lease_owner=? AND status='running'",
                      (db.now() + lease_sec, job_id, worker_id))
        return r.rowcount == 1


def _finish(job_id: int, worker_id: str | None, status: str, **fields: Any) -> bool:
    sets = ["status=?", "updated_at=?", "lease_owner=NULL", "lease_expires_at=NULL"]
    vals: list[Any] = [status, db.now()]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(db.jdump(v) if k in ("result", "checkpoint") and not isinstance(v, str) else v)
    where = "id=?"
    vals.append(job_id)
    if worker_id:
        where += " AND lease_owner=?"
        vals.append(worker_id)
    with db.connect() as c:
        return c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE {where}", vals).rowcount == 1


def complete(job: dict, worker_id: str, result: Any = None) -> None:
    _finish(job["id"], worker_id, "done", result=result, error=None, error_kind=None, wait_reason=None)
    emit("job.done", f"Hoàn thành {job['kind']}", team_id=job["team_id"], job_id=job["id"], request_id=job["request_id"],
         data={"kind": job["kind"]})


def wait(job: dict, worker_id: str | None, status: str, reason: str, run_at: float | None = None,
         error_kind: str | None = None) -> None:
    assert status in ("awaiting_approval", "awaiting_connection", "awaiting_quota", "needs_reconcile", "queued")
    _finish(job["id"], worker_id, status, wait_reason=reason, next_run_at=run_at or db.now(),
            error_kind=error_kind)
    if worker_id and status != "needs_reconcile":
        # lần nhận việc này kết thúc bằng CHỜ, không phải lỗi → không tính vào số lần thử
        db.execute("UPDATE jobs SET attempts=MAX(0, attempts-1) WHERE id=?", job["id"])
    emit("job." + status, f"{job['kind']}: {STATUS_VI[status]} — {reason}", team_id=job["team_id"],
         job_id=job["id"], request_id=job["request_id"], level="warn" if status != "queued" else "info")


def retry_or_fail(job: dict, worker_id: str, kind: str, message: str, backoff: float) -> str:
    if job["attempts"] < job["max_attempts"]:
        _finish(job["id"], worker_id, "queued", next_run_at=db.now() + backoff, error_kind=kind, error=message,
                wait_reason=f"thử lại sau {int(backoff)}s (lần {job['attempts']}/{job['max_attempts']})")
        emit("job.retry", f"{job['kind']} lỗi ({kind}), sẽ thử lại sau {int(backoff)}s: {message[:200]}",
             team_id=job["team_id"], job_id=job["id"], request_id=job["request_id"], level="warn")
        return "queued"
    fail(job, worker_id, kind, message)
    return "failed"


def fail(job: dict, worker_id: str | None, kind: str, message: str) -> None:
    _finish(job["id"], worker_id, "failed", error_kind=kind, error=message)
    emit("job.failed", f"{job['kind']} thất bại ({kind}): {message[:300]}", team_id=job["team_id"],
         job_id=job["id"], request_id=job["request_id"], level="error")


def cancel(job_id: int) -> str:
    j = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
    if not j:
        return "missing"
    if j["status"] == "running":
        db.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?", db.now(), job_id)
        emit("job.cancel_requested", f"Yêu cầu hủy {j['kind']}", team_id=j["team_id"], job_id=job_id)
        return "cancel_requested"
    if j["status"] in OPEN_STATUSES:
        _finish(job_id, None, "cancelled")
        emit("job.cancelled", f"Đã hủy {j['kind']}", team_id=j["team_id"], job_id=job_id, request_id=j["request_id"])
        return "cancelled"
    return j["status"]


def retry(job_id: int) -> bool:
    """Người dùng bấm 'Thử lại'. Việc có tác động đang cần đối chiếu thì không cho thử lại mù."""
    j = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
    if not j or j["status"] not in ("failed", "cancelled", "awaiting_connection", "awaiting_quota"):
        return False
    with db.connect() as c:
        c.execute("UPDATE jobs SET status='queued', attempts=0, next_run_at=?, cancel_requested=0, error=NULL, "
                  "error_kind=NULL, wait_reason=NULL, updated_at=? WHERE id=?", (db.now(), db.now(), job_id))
    emit("job.retry_manual", f"Thử lại {j['kind']} theo yêu cầu", team_id=j["team_id"], job_id=job_id)
    return True


def is_cancel_requested(job_id: int) -> bool:
    r = db.q1("SELECT cancel_requested FROM jobs WHERE id=?", job_id)
    return bool(r and r["cancel_requested"])


def save_checkpoint(job_id: int, checkpoint: dict) -> None:
    db.execute("UPDATE jobs SET checkpoint=?, updated_at=? WHERE id=?", db.jdump(checkpoint), db.now(), job_id)


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def live_workers(stale_sec: float = 60) -> list[dict]:
    ws = db.qa("SELECT * FROM workers WHERE stopped_at IS NULL AND heartbeat_at > ?", db.now() - stale_sec)
    return [w for w in ws if _pid_alive(w["pid"])]


def recover(current_worker: str) -> dict:
    """Khởi động lại sau tắt máy/mất điện: việc 'running' của worker đã chết không được coi là còn chạy.
    - việc không có tác động: đưa lại hàng đợi
    - việc có tác động: chuyển 'Cần đối chiếu' (trừ khi checkpoint cho phép hỏi lại an toàn)"""
    alive = {w["id"] for w in live_workers()} | {current_worker}
    stats = {"requeued": 0, "reconcile": 0}
    for j in db.qa("SELECT * FROM jobs WHERE status='running'"):
        if j["lease_owner"] in alive and (j["lease_expires_at"] or 0) > db.now():
            continue
        cp = db.jload(j["checkpoint"], {})
        if j["side_effect"] and not cp.get("safe_to_resume"):
            wait(j, None, "needs_reconcile", "Bị gián đoạn khi đang thực hiện thao tác bên ngoài; cần đối chiếu trước khi chạy lại")
            stats["reconcile"] += 1
        else:
            _finish(j["id"], None, "queued", wait_reason="khôi phục sau gián đoạn", next_run_at=db.now())
            emit("job.recovered", f"Khôi phục {j['kind']} sau gián đoạn", team_id=j["team_id"], job_id=j["id"])
            stats["requeued"] += 1
    with db.connect() as c:
        c.execute("UPDATE cli_runs SET status='interrupted', ended_at=? WHERE status='running'", (db.now(),))
        c.execute("UPDATE employees SET status='idle', current_job_id=NULL")
        c.execute("UPDATE workers SET stopped_at=? WHERE stopped_at IS NULL AND id NOT IN (%s)"
                  % ",".join("?" * len(alive)), (db.now(), *alive))
    return stats


def wake_due_waits() -> None:
    """Việc chờ lượt sử dụng / chờ kết nối đến hạn kiểm tra lại → đưa về hàng đợi."""
    ts = db.now()
    with db.connect() as c:
        c.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status IN ('awaiting_quota') AND next_run_at<=?",
                  (ts, ts))
        c.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status='awaiting_connection' "
                  "AND next_run_at<=? AND next_run_at>0", (ts, ts))


def counts() -> dict:
    out = {k: 0 for k in STATUS_VI}
    for r in db.qa("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
        out[r["status"]] = r["n"]
    return out
