"""MOCK — CLI giả lập dùng trong kiểm thử. KHÔNG phải Claude Code/Antigravity CLI thật.

Đọc kịch bản phản hồi từ file JSON (FAKE_CLI_SCRIPT) theo thứ tự, mô phỏng định dạng
stream-json của từng CLI (Claude: kiểm chứng trên bản thật; Antigravity: theo tài liệu headless chính thức).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path


def _next_step(flavor: str) -> dict:
    p = Path(os.environ["FAKE_CLI_SCRIPT"])
    data = json.loads(p.read_text(encoding="utf-8"))
    q = data.get(flavor) or []
    step = q.pop(0) if q else {"type": "ok", "structured": {}}
    data[flavor] = q
    p.write_text(json.dumps(data), encoding="utf-8")
    return step


def out(d: dict) -> None:
    sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(flavor: str) -> None:
    prompt = sys.stdin.read()
    if flavor == "antigravity":  # stdin dạng stream-json: {"event": "user", "message": {"content": ...}}
        prompt = json.loads(prompt.splitlines()[0])["message"]["content"]
    log = os.environ.get("FAKE_CLI_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"flavor": flavor, "argv": sys.argv[1:], "prompt": prompt}) + "\n")
    step = _next_step(flavor)
    sid = str(uuid.uuid4())
    t = step["type"]
    if t == "sleep":
        time.sleep(step.get("sec", 60))
    if flavor == "claude":
        out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-claude-model", "tools": []})
        if t == "quota":
            out({"type": "rate_limit_event", "session_id": sid, "rate_limit_info": {"status": "rejected", "resetsAt": int(time.time()) + 3600}})
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "You've hit your limit", "session_id": sid, "api_error_status": 429})
            sys.exit(1)
        if t == "auth":
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Not logged in · Please run /login", "session_id": sid, "api_error_status": 401})
            sys.exit(1)
        for i, tool in enumerate(step.get("tools", [])):
            tid = f"tu{i}"
            out({"type": "assistant", "session_id": sid, "message": {"content": [{"type": "tool_use", "id": tid, "name": tool["name"], "input": tool.get("input", {})}]}})
            out({"type": "user", "session_id": sid, "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": tool.get("output", ""), "is_error": False}]}})
        if t == "text":
            out({"type": "result", "subtype": "success", "is_error": False, "result": step["text"], "session_id": sid})
        else:
            out({"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": step.get("structured"), "session_id": sid, "modelUsage": {"fake-claude-model": {}}})
    else:
        # định dạng thật của agy: nội dung lồng dưới khóa trùng tên sự kiện
        cid = sid
        ev = lambda name, body: out({"event": name, name: {"conversation_id": cid, **body}})  # noqa: E731
        ev("init", {"cwd": os.getcwd(), "tools": ["search_web", "read_url_content", "run_command"], "permission_mode": "request-review"})
        if t == "auth":
            sys.stderr.write("Error: failed to retrieve token: secret keyring is locked\n")
            ev("result", {"status": "ERROR", "error": "authentication required"})
            sys.exit(1)
        if t == "quota":
            ev("result", {"status": "ERROR", "error": {"message": "RESOURCE_EXHAUSTED: quota exceeded"}})
            sys.exit(1)
        if t == "denied":  # agy thật: lệnh bị từ chối → kết thúc lượt, response rỗng, mã thoát 0
            info = {"name": "run_command", "parameters": {"CommandLine": "dir"}}
            ev("step_update", {"step_index": 1, "state": "ACTIVE", "step_type": "tool", "tool_name": "run_command", "tool_info": info})
            ev("step_update", {"step_index": 1, "state": "ERROR", "step_type": "tool", "tool_name": "run_command",
                               "tool_info": {**info, "error": {"type": "TOOL_ERROR", "message": "permission check failed"}}})
            sys.stderr.write('jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.\n')
            ev("result", {"status": "SUCCESS", "response": "", "num_turns": 1, "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
            sys.exit(0)
        for i, tool in enumerate(step.get("tools", [])):
            base = {"step_index": i + 1, "step_type": "tool", "tool_name": tool["name"]}
            ev("step_update", {**base, "state": "ACTIVE", "tool_info": {"name": tool["name"], "parameters": tool.get("input", {})}})
            ev("step_update", {**base, "state": "DONE", "tool_info": {"name": tool["name"], "parameters": tool.get("input", {}), "output": tool.get("output", "")}})
        text = step["text"] if t == "text" else json.dumps(step.get("structured"), ensure_ascii=False)
        ev("step_update", {"step_index": 99, "state": "DONE", "step_type": "agent_response", "text_delta": text})
        res = {"status": "SUCCESS", "response": text, "num_turns": 1}
        if t != "text" and any(a == "--json-schema" for a in sys.argv):
            res["structured_output"] = step.get("structured")
        ev("result", res)
    sys.exit(0)
