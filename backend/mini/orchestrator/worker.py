"""Worker nền: nhận việc từ hàng đợi, chạy handler.

Chạy độc lập với giao diện: đóng tab trình duyệt không ảnh hưởng. Tắt máy thì worker
dừng; lần khởi động sau `recover()` đối chiếu và tiếp tục.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .. import db
from ..config import get_settings
from ..events import emit
from . import pipeline, queue, teams
from .errors import (
    Cancelled, Fatal, NeedsReconcile, Reschedule, Retryable, WaitApproval, WaitConnection, WaitQuota,
)

BACKOFF = [30, 120, 600, 1800]


class AlreadyRunning(RuntimeError):
    pass


def execute_job(job: dict, worker_id: str) -> str:
    """Chạy một việc đã nhận (status=running, lease thuộc worker_id). Trả trạng thái cuối."""
    s = get_settings()
    stop = threading.Event()

    def hb() -> None:
        queue.heartbeat(job["id"], worker_id, s.lease_sec)

    def hb_loop() -> None:
        while not stop.wait(max(5, s.lease_sec // 3)):
            hb()

    t = threading.Thread(target=hb_loop, daemon=True)
    t.start()
    try:
        handler = pipeline.HANDLERS.get(job["kind"])
        if not handler:
            queue.fail(job, worker_id, "unknown_kind", f"Không có xử lý cho {job['kind']}")
            return "failed"
        emit("job.start", f"Bắt đầu: {pipeline.JOB_VI.get(job['kind'], job['kind'])}", team_id=job["team_id"],
             job_id=job["id"], request_id=job["request_id"])
        ctx = pipeline.Ctx(job, worker_id, hb)
        result = handler(ctx)
        queue.complete(job, worker_id, result)
        return "done"
    except WaitApproval as e:
        p = db.jload(job["payload"], {})
        p["approval_id"] = e.approval_id
        db.execute("UPDATE jobs SET payload=? WHERE id=?", db.jdump(p), job["id"])
        queue.wait(job, worker_id, "awaiting_approval", str(e))
        return "awaiting_approval"
    except WaitQuota as e:
        queue.wait(job, worker_id, "awaiting_quota", str(e), run_at=e.until, error_kind="quota")
        return "awaiting_quota"
    except WaitConnection as e:
        queue.wait(job, worker_id, "awaiting_connection", str(e), run_at=e.recheck_at or 0, error_kind="connection")
        return "awaiting_connection"
    except NeedsReconcile as e:
        queue.wait(job, worker_id, "needs_reconcile", str(e), error_kind="uncertain")
        return "needs_reconcile"
    except Reschedule as e:
        queue.wait(job, worker_id, "queued", str(e), run_at=db.now() + e.delay)  # wait() không tính là lần thử
        return "queued"
    except Cancelled as e:
        queue._finish(job["id"], worker_id, "cancelled", error=str(e))
        emit("job.cancelled", f"Đã hủy {job['kind']}", team_id=job["team_id"], job_id=job["id"])
        return "cancelled"
    except Retryable as e:
        b = BACKOFF[min(job["attempts"] - 1, len(BACKOFF) - 1)]
        st = queue.retry_or_fail(job, worker_id, e.kind, str(e), b)
        if st == "failed":
            pipeline.on_job_failed(job, str(e))
        return st
    except Fatal as e:
        queue.fail(job, worker_id, e.kind, str(e))
        pipeline.on_job_failed(job, str(e))
        return "failed"
    except Exception as e:  # noqa: BLE001 — lỗi lập trình: ghi đủ để sửa, thử lại có giới hạn
        tb = traceback.format_exc()[-3000:]
        emit("job.exception", f"Lỗi không lường trước ở {job['kind']}: {e}", team_id=job["team_id"],
             job_id=job["id"], level="error", data={"traceback": tb})
        st = queue.retry_or_fail(job, worker_id, "exception", f"{type(e).__name__}: {e}", BACKOFF[0])
        if st == "failed":
            pipeline.on_job_failed(job, f"{type(e).__name__}: {e}")
        return st
    finally:
        stop.set()


# --- lập lịch định kỳ -----------------------------------------------------------


def schedule_tick() -> None:
    """Nhân viên chỉ làm theo yêu cầu của bạn — không tự đề xuất việc. Định kỳ chỉ đánh thức việc chờ đến hạn."""
    queue.wake_due_waits()


class Worker:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.stop = threading.Event()
        s = get_settings()
        self.slots = s.max_concurrent_cli + 2
        self.pool = ThreadPoolExecutor(max_workers=self.slots, thread_name_prefix="job")
        self.running: set[int] = set()
        self.lock = threading.Lock()

    def register(self) -> None:
        others = [w for w in queue.live_workers() if w["id"] != self.id]
        if others:
            raise AlreadyRunning(f"Đã có worker đang chạy (pid {others[0]['pid']})")
        db.execute("INSERT INTO workers(id, pid, started_at, heartbeat_at) VALUES(?,?,?,?)",
                   self.id, os.getpid(), db.now(), db.now())
        stats = queue.recover(self.id)
        emit("worker.start", f"Worker khởi động; khôi phục: {stats['requeued']} việc đưa lại hàng đợi, "
                             f"{stats['reconcile']} việc cần đối chiếu", data=stats)

    def _run(self, job: dict) -> None:
        try:
            execute_job(job, self.id)
        finally:
            with self.lock:
                self.running.discard(job["id"])

    def loop(self) -> None:
        s = get_settings()
        last_tick = 0.0
        stop_file = s.data_dir / "worker.stop"
        while not self.stop.is_set():
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                emit("worker.stop_requested", "Nhận yêu cầu dừng worker từ launcher")
                break
            db.execute("UPDATE workers SET heartbeat_at=? WHERE id=?", db.now(), self.id)
            running = bool(db.get_setting("company_running"))
            if running and time.time() - last_tick > 30:
                try:
                    schedule_tick()
                except Exception as e:  # noqa: BLE001
                    emit("scheduler.error", f"Lỗi lập lịch: {e}", level="error")
                last_tick = time.time()
            if running:
                while True:
                    with self.lock:
                        if len(self.running) >= self.slots:
                            break
                    job = queue.claim(self.id, s.lease_sec)
                    if not job:
                        break
                    with self.lock:
                        self.running.add(job["id"])
                    self.pool.submit(self._run, job)
            self.stop.wait(2)

    def shutdown(self) -> None:
        self.stop.set()
        # việc đang chạy: yêu cầu hủy để tiến trình CLI con được dừng gọn
        with self.lock:
            ids = list(self.running)
        for jid in ids:
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", jid)
        self.pool.shutdown(wait=True, cancel_futures=True)
        for jid in ids:  # đưa lại hàng đợi để lần sau làm tiếp (không phải hủy hẳn)
            j = db.q1("SELECT * FROM jobs WHERE id=?", jid)
            if j and j["status"] == "cancelled" and j["side_effect"] == 0:
                db.execute("UPDATE jobs SET status='queued', cancel_requested=0, wait_reason='tiếp tục sau khi dừng worker' "
                           "WHERE id=?", jid)
        db.execute("UPDATE workers SET stopped_at=? WHERE id=?", db.now(), self.id)
        emit("worker.stop", "Worker dừng")


def main() -> None:
    db.migrate()
    teams.ensure_staff()
    teams.ensure_workspace()
    w = Worker()
    w.register()
    try:
        w.loop()
    except KeyboardInterrupt:
        pass
    finally:
        w.shutdown()
