"""Adapter CLI (dùng CLI giả lập — MOCK — qua tiến trình con thật): định dạng, lỗi, hết lượt,
JSON không hợp lệ, timeout, hủy, an toàn đối số và môi trường."""
import json
import threading
import time

from mini import db
from mini.adapters.cli_base import CliRequest, classify_text, clean_env
from mini.adapters.claude_cli import ClaudeAdapter
from mini.adapters.antigravity_cli import AntigravityAdapter, canonical_tool
from mini.orchestrator import ai
from helpers import LEAD, new_request, run_next

SCHEMA = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}


def req(env, **kw):
    return CliRequest(prompt=kw.pop("prompt", "hãy làm"), cwd=env.tmp / "w", log_dir=env.tmp / "logs", **kw)


def test_claude_ok_structured(env):
    env.queue_cli("claude", {"type": "ok", "structured": {"x": 5}})
    r = ClaudeAdapter(env.s.cli_paths["claude"]).run(req(env, json_schema=SCHEMA))
    assert r.ok and r.data == {"x": 5} and r.session_id and r.observed_model == "fake-claude-model"


def test_antigravity_ok_structured_and_tools(env):
    env.queue_cli("antigravity", {"type": "ok", "structured": {"x": 2}, "tools": [
        {"name": "search_web", "input": {"query": "q"}, "output": "abc"},
        {"name": "read_url_content", "input": {"Url": "https://example.org/a"}, "output": "nội dung"}]})
    r = AntigravityAdapter(env.s.cli_paths["antigravity"]).run(req(env, json_schema=SCHEMA, tools=["web_search"]))
    assert r.ok and r.data == {"x": 2} and r.session_id
    assert [t.name for t in r.tool_calls] == ["web_search", "web_fetch"] and all(t.ok for t in r.tool_calls)
    assert r.tool_calls[1].input == {"Url": "https://example.org/a"}
    argv = env.cli_log()[-1]["argv"]
    assert "--input-format" in argv and "-p" not in argv and "--json-schema" in argv
    assert "--dangerously-skip-permissions" not in argv


def test_antigravity_headless_rules_and_denied_tool(env):
    env.queue_cli("antigravity", {"type": "denied"})
    a = AntigravityAdapter(env.s.cli_paths["antigravity"])
    r = a.run(req(env, json_schema=SCHEMA, prompt="nhiệm vụ"))
    assert not r.ok and r.kind == "tool_denied" and "RunCommand" in r.message
    assert [t.name for t in r.tool_calls] == ["run_command"] and r.tool_calls[0].ok is False
    sent = env.cli_log()[-1]["prompt"]
    assert sent.endswith("nhiệm vụ") and "KHÔNG dùng run_command" in sent and "không cần công cụ nào" in sent
    env.queue_cli("antigravity", {"type": "ok", "structured": {"x": 1}})
    a.run(req(env, json_schema=SCHEMA, tools=["web_search", "web_fetch"]))
    assert "search_web và read_url_content" in env.cli_log()[-1]["prompt"]


def test_antigravity_canonical_tool_names():
    assert canonical_tool("search_web") == "web_search" and canonical_tool("google_web_search") == "web_search"
    assert canonical_tool("read_url_content") == "web_fetch" and canonical_tool("view_file") == "view_file"


def test_antigravity_refuses_paid_credits_and_api_key(env):
    from mini.adapters import antigravity_cli
    a = AntigravityAdapter(env.s.cli_paths["antigravity"])
    for cfg in ({"useG1Credits": True}, {"modelProvider": "gemini"}, {"auth": {"type": "gemini-api-key"}}):
        antigravity_cli.SETTINGS_PATH.write_text(json.dumps(cfg), encoding="utf-8")
        n = len(env.cli_log())
        r = a.run(req(env))
        assert not r.ok and r.kind == "auth" and len(env.cli_log()) == n  # không gọi CLI
        assert a.auth_status()["billing_problem"]
    antigravity_cli.SETTINGS_PATH.write_text(json.dumps({"useG1Credits": False}), encoding="utf-8")
    assert a.auth_status()["billing_problem"] is None


def test_invalid_json_detected(env):
    env.queue_cli("antigravity", {"type": "text", "text": "Xin lỗi, tôi không thể"})
    r = AntigravityAdapter(env.s.cli_paths["antigravity"]).run(req(env, json_schema=SCHEMA))
    assert not r.ok and r.kind == "invalid_output"
    env.queue_cli("claude", {"type": "ok", "structured": {"x": "không phải số"}})
    r = ClaudeAdapter(env.s.cli_paths["claude"]).run(req(env, json_schema=SCHEMA))
    assert r.kind == "invalid_output"


def test_quota_and_auth_classification(env):
    env.queue_cli("claude", {"type": "quota"}, {"type": "auth"})
    a = ClaudeAdapter(env.s.cli_paths["claude"])
    r1 = a.run(req(env))
    assert r1.kind == "quota" and r1.quota_reset_at > time.time()
    r2 = a.run(req(env))
    assert r2.kind == "auth"
    env.queue_cli("antigravity", {"type": "auth"}, {"type": "quota"})
    g = AntigravityAdapter(env.s.cli_paths["antigravity"])
    assert g.run(req(env)).kind == "auth"
    assert g.run(req(env)).kind == "quota"
    assert classify_text("getaddrinfo ENOTFOUND api.anthropic.com") == "network"


def test_timeout_kills_process(env):
    env.queue_cli("claude", {"type": "sleep", "sec": 30})
    t0 = time.time()
    r = ClaudeAdapter(env.s.cli_paths["claude"]).run(req(env, timeout_sec=2))
    assert r.kind == "timeout" and time.time() - t0 < 20


def test_cancel_kills_process(env):
    env.queue_cli("antigravity", {"type": "sleep", "sec": 30})
    flag = threading.Event()
    threading.Timer(1.0, flag.set).start()
    t0 = time.time()
    r = AntigravityAdapter(env.s.cli_paths["antigravity"]).run(req(env, cancel_check=flag.is_set))
    assert r.kind == "cancelled" and time.time() - t0 < 20


def test_prompt_never_in_argv_and_billing_env_removed(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-pass")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-should-not-pass")
    evil = 'bình luận"; rm -rf / & echo pwned'
    env.queue_cli("claude", {"type": "ok", "structured": {"x": 1}})
    ClaudeAdapter(env.s.cli_paths["claude"]).run(req(env, prompt=evil, json_schema=SCHEMA))
    log = env.cli_log()[-1]
    assert evil in log["prompt"] and all(evil not in a for a in log["argv"])
    assert "--dangerously-skip-permissions" not in log["argv"] and "bypassPermissions" not in log["argv"]
    e, removed = clean_env("claude")
    assert "ANTHROPIC_API_KEY" not in e and "ANTHROPIC_API_KEY" in removed
    e, removed = clean_env("antigravity")
    assert "GEMINI_API_KEY" not in e and "GEMINI_API_KEY" in removed
    env.queue_cli("antigravity", {"type": "ok", "structured": {"x": 1}})
    AntigravityAdapter(env.s.cli_paths["antigravity"]).run(req(env, prompt=evil, json_schema=SCHEMA))
    log = env.cli_log()[-1]
    assert log["prompt"].endswith(evil) and all(evil not in a for a in log["argv"])


def test_quota_puts_job_waiting_and_blocks_provider(env):
    env.queue_cli("claude", {"type": "quota"})
    new_request(env)
    st, job = run_next()
    assert st == "awaiting_quota" and job["next_run_at"] > time.time() + 60
    assert ai.provider_blocked_until("claude")
    # việc Claude khác cũng chờ, không chạy CLI
    n_runs = db.q1("SELECT COUNT(*) n FROM cli_runs")["n"]
    new_request(env)
    st2, _ = run_next()
    assert st2 == "awaiting_quota" and db.q1("SELECT COUNT(*) n FROM cli_runs")["n"] == n_runs


def test_auth_error_waits_for_connection_no_silent_fallback(env):
    db.execute("UPDATE employees SET fallback_cli='antigravity' WHERE id='NV-LEAD'")
    env.queue_cli("claude", {"type": "auth"})
    new_request(env)
    st, job = run_next()
    assert st == "awaiting_connection"
    assert not db.qa("SELECT * FROM cli_runs WHERE cli='antigravity'")  # dự phòng tắt mặc định


def test_explicit_fallback_with_handoff(env):
    db.set_setting("allow_cli_fallback", True)
    db.execute("UPDATE employees SET fallback_cli='antigravity' WHERE id='NV-LEAD'")
    env.queue_cli("claude", {"type": "quota"})
    env.queue_cli("antigravity", {"type": "ok", "structured": LEAD})
    new_request(env)
    st, _ = run_next()
    assert st == "done"
    h = db.q1("SELECT * FROM handoffs")
    assert h and h["from_cli"] == "claude" and h["to_cli"] == "antigravity" and "BẢN BÀN GIAO" in h["content"]


def test_invalid_output_retries_then_fails(env):
    env.queue_cli("antigravity", *[{"type": "text", "text": "không phải json"}] * 6)
    db.execute("UPDATE employees SET cli='antigravity' WHERE id='NV-LEAD'")
    rid = new_request(env)["id"]
    jid = db.q1("SELECT id FROM jobs WHERE request_id=?", rid)["id"]
    db.execute("UPDATE jobs SET max_attempts=2 WHERE id=?", jid)
    st, job = run_next()
    assert st == "queued" and job["error_kind"] == "invalid_output"
    db.execute("UPDATE jobs SET next_run_at=0 WHERE id=?", jid)
    st, job = run_next()
    assert st == "failed" and job["attempts"] == 2
