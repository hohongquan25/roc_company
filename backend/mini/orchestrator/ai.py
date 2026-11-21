"""Chạy một nhân viên AI cho một nhiệm vụ: chọn CLI, giới hạn đồng thời, ghi cli_runs,
xử lý phiên, hết lượt, lỗi xác thực, kết quả không hợp lệ và dự phòng (chỉ khi bật rõ ràng)."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..adapters.claude_cli import ClaudeAdapter
from ..adapters.cli_base import CliAdapter, CliRequest, CliResult
from ..adapters.antigravity_cli import AntigravityAdapter
from ..config import get_settings
from ..events import emit
from . import queue
from .errors import Cancelled, Retryable, WaitConnection, WaitQuota

ADAPTERS: dict[str, type[CliAdapter]] = {"claude": ClaudeAdapter, "antigravity": AntigravityAdapter}
CLI_LABEL = {"claude": "Claude Code", "antigravity": "Antigravity CLI"}

_global_sem: threading.BoundedSemaphore | None = None
_cli_sems: dict[str, threading.BoundedSemaphore] = {}
_sem_lock = threading.Lock()


def get_adapter(cli: str) -> CliAdapter:
    if cli not in ADAPTERS:
        raise ValueError(f"CLI không được hỗ trợ: {cli}")
    return ADAPTERS[cli](get_settings().cli_paths.get(cli))


def _sems(cli: str) -> tuple[threading.BoundedSemaphore, threading.BoundedSemaphore]:
    global _global_sem
    s = get_settings()
    with _sem_lock:
        if _global_sem is None:
            _global_sem = threading.BoundedSemaphore(s.max_concurrent_cli)
        if cli not in _cli_sems:
            _cli_sems[cli] = threading.BoundedSemaphore(s.max_concurrent_per_cli.get(cli, 1))
        return _global_sem, _cli_sems[cli]


def reset_limits() -> None:
    global _global_sem
    with _sem_lock:
        _global_sem = None
        _cli_sems.clear()


def provider_blocked_until(cli: str) -> float | None:
    r = db.q1("SELECT blocked_until FROM provider_state WHERE cli=?", cli)
    if r and r["blocked_until"] and r["blocked_until"] > db.now():
        return r["blocked_until"]
    return None


def block_provider(cli: str, until: float, reason: str) -> None:
    db.execute("INSERT INTO provider_state(cli, blocked_until, reason, updated_at) VALUES(?,?,?,?) "
               "ON CONFLICT(cli) DO UPDATE SET blocked_until=excluded.blocked_until, reason=excluded.reason, "
               "updated_at=excluded.updated_at", cli, until, reason[:500], db.now())


def _session_for(employee_id: str, cli: str, scope_key: str) -> str | None:
    r = db.q1("SELECT session_id FROM cli_sessions WHERE employee_id=? AND cli=? AND scope_key=?",
              employee_id, cli, scope_key)
    return r["session_id"] if r else None


def _save_session(team_id: int | None, employee_id: str, cli: str, scope_key: str, sid: str) -> None:
    db.execute("INSERT INTO cli_sessions(team_id, employee_id, cli, scope_key, session_id, updated_at) "
               "VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id, cli, scope_key) DO UPDATE SET "
               "session_id=excluded.session_id, updated_at=excluded.updated_at",
               team_id, employee_id, cli, scope_key, sid, db.now())


def _run_once(*, job: dict, employee: dict, cli: str, prompt: str, schema: dict | None, tools: list[str],
              workdir: Path, resume: str | None, cancel_check: Callable[[], bool], timeout: int) -> CliResult:
    s = get_settings()
    adapter = get_adapter(cli)
    run_id = db.execute(
        "INSERT INTO cli_runs(job_id, employee_id, cli, model, resumed, status, started_at) VALUES(?,?,?,?,?,?,?)",
        job["id"], employee["id"], cli, employee.get("model") if cli == employee["cli"] else None,
        int(bool(resume)), "running", db.now(),
    )
    log_dir = s.logs_dir / "cli_runs" / str(run_id)

    def on_event(kind: str, data: dict) -> None:
        if kind == "tool_use":
            emit("cli.tool", f"{employee['name']} dùng công cụ {data.get('name')}", team_id=job["team_id"],
                 job_id=job["id"], request_id=job["request_id"], data=data)
        elif kind == "init":
            emit("cli.start", f"{employee['name']} bắt đầu ({CLI_LABEL[cli]}, model {data.get('model')})",
                 team_id=job["team_id"], job_id=job["id"], request_id=job["request_id"])

    req = CliRequest(
        prompt=prompt, cwd=workdir, log_dir=log_dir,
        model=employee.get("model") if cli == employee["cli"] else None,
        tools=tools, json_schema=schema, resume_session_id=resume, timeout_sec=timeout,
        cancel_check=cancel_check, on_event=on_event,
        on_start=lambda pid: db.execute("UPDATE cli_runs SET pid=? WHERE id=?", pid, run_id),
    )
    (log_dir).mkdir(parents=True, exist_ok=True)
    (log_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    res = adapter.run(req)
    db.execute(
        "UPDATE cli_runs SET status=?, error_kind=?, exit_code=?, session_id=?, stdout_path=?, stderr_path=?, "
        "tool_calls=?, observed_model=?, args=?, ended_at=? WHERE id=?",
        "ok" if res.ok else "error", None if res.ok else res.kind, res.exit_code, res.session_id,
        res.stdout_path, res.stderr_path, db.jdump([t.__dict__ for t in res.tool_calls])[:50000],
        res.observed_model, db.jdump([a if len(a) < 300 else a[:300] + "…" for a in res.args]), db.now(), run_id,
    )
    return res


def run_employee(*, job: dict, employee: dict, prompt: str, schema: dict | None, workdir: Path,
                 scope_key: str, cancel_check: Callable[[], bool], resume: bool = False,
                 handoff_context: str = "") -> CliResult:
    """Trả CliResult thành công hoặc ném tín hiệu (WaitQuota/WaitConnection/Retryable/Cancelled)."""
    s = get_settings()
    cli = employee["cli"]
    tools = db.jload(employee["allowed_tools"], [])
    order = [cli]
    if employee.get("fallback_cli") and db.get_setting("allow_cli_fallback") and employee["fallback_cli"] != cli:
        order.append(employee["fallback_cli"])
    last: CliResult | None = None
    for idx, c in enumerate(order):
        until = provider_blocked_until(c)
        if until:
            if idx + 1 < len(order):
                continue
            raise WaitQuota(until, f"{CLI_LABEL[c]} đang hết lượt, chờ tới khi được dùng lại")
        p = prompt
        if idx > 0:
            p = _handoff(job, cli, c, prompt, last, handoff_context)
        sid = _session_for(employee["id"], c, scope_key) if (resume and c == "claude") else None
        g, per = _sems(c)
        db.execute("UPDATE employees SET status='waiting_slot', current_job_id=? WHERE id=?", job["id"], employee["id"])
        with per, g:
            if cancel_check():
                raise Cancelled("đã hủy trước khi chạy")
            db.execute("UPDATE employees SET status='working' WHERE id=?", employee["id"])
            try:
                res = _run_once(job=job, employee=employee, cli=c, prompt=p, schema=schema, tools=tools,
                                workdir=workdir, resume=sid, cancel_check=cancel_check, timeout=s.cli_timeout_sec)
                if res.kind == "invalid_output" and res.session_id and c == "claude":
                    emit("cli.repair", f"{employee['name']}: kết quả sai định dạng, yêu cầu sửa một lần",
                         team_id=job["team_id"], job_id=job["id"], level="warn")
                    res = _run_once(job=job, employee=employee, cli=c, schema=schema, tools=tools, workdir=workdir,
                                    prompt=f"Kết quả trước không hợp lệ: {res.message}. Trả lại đúng một JSON theo schema.",
                                    resume=res.session_id, cancel_check=cancel_check, timeout=s.cli_timeout_sec)
            finally:
                queue.release_employee(employee["id"], job["id"])
        if res.session_id and c == "claude":
            _save_session(job["team_id"], employee["id"], c, scope_key, res.session_id)
        if res.ok:
            return res
        last = res
        if res.kind == "cancelled":
            raise Cancelled(res.message)
        if res.kind == "quota":
            until = res.quota_reset_at if (res.quota_reset_at and res.quota_reset_at > db.now()) else db.now() + s.quota_wait_sec
            block_provider(c, until, res.message)
            emit("provider.quota", f"{CLI_LABEL[c]} hết lượt sử dụng; lưu tiến độ và chờ", job_id=job["id"],
                 team_id=job["team_id"], level="warn", data={"until": until})
            continue
        if res.kind in ("auth", "not_installed"):
            emit("provider.auth", f"{CLI_LABEL[c]}: {res.message[:200]}", job_id=job["id"], team_id=job["team_id"], level="warn")
            continue
        break  # lỗi mạng / không hợp lệ / timeout: không chuyển công cụ, để cơ chế thử lại xử lý
    assert last is not None
    if last.kind == "quota":
        raise WaitQuota(provider_blocked_until(last.cli) or db.now() + s.quota_wait_sec,
                        f"{CLI_LABEL[last.cli]} hết lượt sử dụng")
    if last.kind in ("auth", "not_installed"):
        raise WaitConnection(f"{CLI_LABEL[last.cli]} chưa sẵn sàng ({last.kind}): {last.message[:300]}",
                             recheck_at=db.now() + 600)
    raise Retryable(last.kind, last.message or last.kind)


def _handoff(job: dict, from_cli: str, to_cli: str, prompt: str, last: CliResult | None, extra: str) -> str:
    content = "\n".join([
        "# BẢN BÀN GIAO",
        f"Nhiệm vụ #{job['id']} ({job['kind']}) chuyển từ {CLI_LABEL[from_cli]} sang {CLI_LABEL[to_cli]} "
        f"vì: {(last.kind if last else 'không rõ')}.",
        "Kết quả đã có: " + (extra or "chưa có kết quả trung gian được lưu."),
        "Việc còn thiếu: hoàn thành toàn bộ nhiệm vụ bên dưới.",
        "",
        prompt,
    ])
    db.execute("INSERT INTO handoffs(job_id, from_cli, to_cli, content, created_at) VALUES(?,?,?,?,?)",
               job["id"], from_cli, to_cli, content, db.now())
    emit("cli.handoff", f"Chuyển nhiệm vụ sang {CLI_LABEL[to_cli]} (dự phòng đã bật)", job_id=job["id"],
         team_id=job["team_id"], level="warn")
    return content


def employee_for(role: str) -> dict:
    """Nhân viên cố định phụ trách vai này (dùng chung cả văn phòng)."""
    e = db.q1("SELECT * FROM employees WHERE role=? ORDER BY rowid LIMIT 1", role)
    if not e:
        raise WaitConnection(f"Chưa có nhân viên vai trò {role}")
    return e


def record_probe(cli: str, res: CliResult) -> None:
    db.set_setting(f"cli_probe_{cli}", {"ok": res.ok, "kind": res.kind, "ts": db.now()})


def last_verified(cli: str) -> dict | None:
    """Kết quả thật gần nhất cho biết CLI đã đăng nhập hay chưa (lần chạy việc hoặc chạy thử)."""
    cands = []
    r = db.q1("SELECT status, error_kind, ended_at FROM cli_runs WHERE cli=? AND ended_at IS NOT NULL AND "
              "(status='ok' OR error_kind IN ('auth','not_installed')) ORDER BY id DESC LIMIT 1", cli)
    if r:
        cands.append({"ok": r["status"] == "ok", "kind": r["error_kind"] or "ok", "ts": r["ended_at"]})
    p = db.get_setting(f"cli_probe_{cli}")
    if p and p.get("kind") in ("ok", "auth", "not_installed"):
        cands.append(p)
    return max(cands, key=lambda x: x["ts"]) if cands else None


def cli_health() -> dict[str, Any]:
    out = {}
    for name in ADAPTERS:
        a = get_adapter(name)
        v = a.version()
        st = a.auth_status() if v.get("installed") else {"logged_in": False}
        if v.get("installed") and st.get("logged_in") is None:  # CLI không có lệnh kiểm tra đăng nhập
            lv = last_verified(name)
            st["last_verified"] = lv
            st["logged_in"] = False if st.get("billing_problem") else (lv["ok"] if lv else None)
        out[name] = {**v, **st, "label": CLI_LABEL[name], "blocked_until": provider_blocked_until(name)}
    return out
