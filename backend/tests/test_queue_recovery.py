"""Hàng đợi: hai worker không nhận trùng; khôi phục sau gián đoạn; hủy."""
import threading
import time

from mini import db
from mini.orchestrator import queue


def test_two_workers_never_claim_same_job(env):
    for i in range(60):
        queue.enqueue("report", idempotency_key=f"k{i}")
    got: dict[str, list[int]] = {"w1": [], "w2": [], "w3": []}

    def grab(w):
        while True:
            j = queue.claim(w, 60)
            if not j:
                return
            got[w].append(j["id"])

    ts = [threading.Thread(target=grab, args=(w,)) for w in got]
    [t.start() for t in ts]
    [t.join() for t in ts]
    all_ids = sum(got.values(), [])
    assert len(all_ids) == 60
    assert len(set(all_ids)) == 60  # không việc nào bị nhận hai lần


def test_idempotency_key_prevents_duplicates(env):
    a = queue.enqueue("report", idempotency_key="same")
    b = queue.enqueue("report", idempotency_key="same")
    assert a == b
    assert db.q1("SELECT COUNT(*) n FROM jobs")["n"] == 1


def test_recover_after_power_loss(env):
    t = env.team()
    safe = queue.enqueue("research", team_id=t["id"], idempotency_key="r1")
    side = queue.enqueue("produce", team_id=t["id"], idempotency_key="s1")
    resumable = queue.enqueue("verify", team_id=t["id"], idempotency_key="u1")
    # việc có tác động ra ngoài (cờ side_effect): không được tự chạy lại khi kết quả chưa rõ
    db.execute("UPDATE jobs SET side_effect=1 WHERE id IN (?,?)", side, resumable)
    # một worker cũ (đã chết) đang giữ các việc
    db.execute("INSERT INTO workers(id, pid, started_at, heartbeat_at) VALUES('dead', 999999, 0, 0)")
    for jid in (safe, side, resumable):
        db.execute("UPDATE jobs SET status='running', lease_owner='dead', lease_expires_at=? WHERE id=?", time.time() + 999, jid)
    queue.save_checkpoint(resumable, {"safe_to_resume": True})
    stats = queue.recover("new-worker")
    assert stats == {"requeued": 2, "reconcile": 1}
    assert db.q1("SELECT status FROM jobs WHERE id=?", safe)["status"] == "queued"
    assert db.q1("SELECT status FROM jobs WHERE id=?", resumable)["status"] == "queued"
    assert db.q1("SELECT status FROM jobs WHERE id=?", side)["status"] == "needs_reconcile"
    # việc cần đối chiếu không được "thử lại" mù
    assert queue.retry(side) is False


def test_live_lease_not_stolen(env):
    jid = queue.enqueue("report", idempotency_key="x")
    j = queue.claim("w-alive", 300)
    assert j["id"] == jid
    db.execute("INSERT INTO workers(id, pid, started_at, heartbeat_at) VALUES('w-alive', ?, ?, ?)",
               __import__("os").getpid(), time.time(), time.time())
    queue.recover("w-other")
    assert db.q1("SELECT status FROM jobs WHERE id=?", jid)["status"] == "running"


def test_cancel_queued_and_running(env):
    a = queue.enqueue("report", idempotency_key="a")
    assert queue.cancel(a) == "cancelled"
    b = queue.enqueue("report", idempotency_key="b")
    queue.claim("w", 60)
    assert queue.cancel(b) == "cancel_requested"
    assert queue.is_cancel_requested(b)


def test_paused_team_jobs_not_claimed(env):
    t = env.team()
    queue.enqueue("lead_plan", team_id=t["id"], idempotency_key="p")
    db.execute("UPDATE teams SET paused=1 WHERE id=?", t["id"])
    assert queue.claim("w", 60) is None
    db.execute("UPDATE teams SET paused=0 WHERE id=?", t["id"])
    assert queue.claim("w", 60) is not None


def test_same_role_parallel_across_requests_serial_within(env):
    a1 = queue.enqueue("write", request_id=1, role="writer", idempotency_key="a1")
    a2 = queue.enqueue("write", request_id=1, role="writer", idempotency_key="a2")
    b1 = queue.enqueue("write", request_id=2, role="writer", idempotency_key="b1")
    first = queue.claim("w", 60)
    second = queue.claim("w", 60)
    assert {first["id"], second["id"]} == {a1, b1}  # hai yêu cầu khác nhau chạy song song
    assert queue.claim("w", 60) is None  # việc thứ hai của yêu cầu 1 vẫn chờ
    assert a2
