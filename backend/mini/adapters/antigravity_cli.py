"""Adapter Antigravity CLI (agy, chế độ headless, stream-json).

Thay Gemini CLI: từ 18/06/2026 Google ngừng phục vụ Gemini CLI cho tài khoản cá nhân
(Gemini Code Assist for individuals, Google AI Pro/Ultra) và hướng dẫn chuyển sang Antigravity CLI.

Theo tài liệu chính thức (antigravity.google/docs/cli/headless, /cli/reference, /permissions):
- Prompt đi qua stdin: --input-format stream-json + --output-format stream-json, mỗi dòng
  {"event": "user", "message": {"content": "..."}}; đóng stdin thì CLI kết thúc sau lượt hiện tại.
  Không dùng -p khi nhập từ stdin.
- Sự kiện ra (trường "event"), nội dung lồng dưới khóa trùng tên sự kiện (kiểm chứng trên agy thật 29/10/2026):
  {"event":"init","conversation_id","init":{cwd, tools, permission_mode, json_schema, model?}},
  {"event":"step_update","step_update":{conversation_id, step_index, state ACTIVE|DONE|ERROR, step_type,
  tool_name, text_delta, tool_info{name, parameters, output, error{type, message}}}},
  {"event":"result","result":{conversation_id, status, response, error, structured_output, usage, denied_actions}}.
- Khi một công cụ cần quyền bị từ chối (vd run_command), agy KẾT THÚC lượt với response rỗng, status SUCCESS,
  stderr "no output produced … auto-denied". Vì vậy prompt gửi Antigravity có thêm chỉ dẫn cấm lệnh terminal.
- status: SUCCESS | ERROR | CANCELED | INTERRUPTED | INVALID | WAITING | RUNNING; lỗi thì mã thoát khác 0.
- --json-schema nhận đường dẫn file; đối tượng đã phân tích nằm ở structured_output.
- Headless không có hỏi quyền: đọc/ghi file trong thư mục làm việc được phép; lệnh shell mặc định
  "Ask" nên bị từ chối mềm. ROC KHÔNG dùng --dangerously-skip-permissions.
- Antigravity không có cờ giới hạn công cụ theo từng lần chạy (quyền nằm trong file cài đặt chung
  ~/.gemini/antigravity-cli/settings.json); ROC không sửa file đó.
- "useG1Credits": true cho phép dùng credit trả phí khi hết hạn mức gói; modelProvider "gemini" +
  GEMINI_API_KEY chuyển sang API key. ROC từ chối chạy khi phát hiện một trong hai.

ROC không dựa vào cơ chế tiếp tục hội thoại của Antigravity: ngữ cảnh luôn gửi lại từ dữ liệu ROC.
Tên công cụ thật: search_web, read_url_content (web); run_command (terminal, bị từ chối ở headless).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .cli_base import (
    CliAdapter, CliRequest, CliResult, StreamParser, classify_text, clean_env, parse_reset_epoch, safe_model,
)

SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
API_AUTH_TYPES = ("gemini-api-key", "agent-platform")

_SEARCH_RE = re.compile(r"search", re.I)
_FETCH_RE = re.compile(r"read_url|url|fetch|browser", re.I)
HEADLESS_RULES = """# Cách làm việc (bắt buộc — chế độ không tương tác)
- Mọi dữ liệu cần thiết đã nằm trong tin nhắn này. KHÔNG khám phá thư mục làm việc, KHÔNG đọc hay ghi file.
- KHÔNG dùng run_command hay bất kỳ lệnh terminal/PowerShell nào: lệnh bị chặn và làm hỏng cả lượt chạy.
- Không mở trình duyệt, không tạo subagent, không hỏi lại người dùng.
{tools}
- Câu trả lời cuối cùng là đúng một đối tượng JSON theo yêu cầu ở cuối tin nhắn.

"""
WEB_TOOLS_RULE = "- Tra cứu web CHỈ bằng search_web và read_url_content."
NO_TOOLS_RULE = "- Nhiệm vụ này không cần công cụ nào: trả lời trực tiếp."

_AUTH_EXTRA_RE = re.compile(r"keyring|failed to retrieve token|no longer supported|not authenticated|login", re.I)


def canonical_tool(name: str | None) -> str:
    """Quy tên công cụ của Antigravity về tên logic để bước nghiên cứu kiểm chứng nguồn."""
    n = name or ""
    if _SEARCH_RE.search(n):
        return "web_search"
    if _FETCH_RE.search(n):
        return "web_fetch"
    return n or "tool"


def read_settings(path: Path | None = None) -> dict | None:
    p = path or SETTINGS_PATH
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {"_unreadable": True}


def billing_problem(settings: dict | None) -> str | None:
    """Lý do không được chạy (dùng credit trả phí / API key), hoặc None."""
    s = settings or {}
    if s.get("useG1Credits") is True:
        return ("Antigravity CLI đang bật 'Use G1 Credits' (dùng credit trả phí khi hết hạn mức gói). "
                "ROC không chạy khi bật — tắt trong agy → /config.")
    if s.get("modelProvider") == "gemini":
        return "Antigravity CLI đang cấu hình modelProvider 'gemini' (dùng GEMINI_API_KEY trả phí). ROC không chạy ở chế độ này."
    if ((s.get("auth") or {}).get("type")) in API_AUTH_TYPES:
        return "Antigravity CLI đang dùng API key / Agent Platform thay vì đăng nhập tài khoản Google. ROC không chạy ở chế độ này."
    return None


class AntigravityAdapter(CliAdapter):
    name = "antigravity"
    exe_name = "agy"

    def argv_prefix(self) -> list[str] | None:
        pre = super().argv_prefix()
        if pre or self.override_path:
            return pre
        # trình cài chính thức đặt agy ở %LOCALAPPDATA%\agy\bin, có thể chưa nằm trong PATH của ROC
        p = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
        return [str(p)] if p.exists() else None

    def build_args(self, req: CliRequest) -> list[str]:
        args = [
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--disable-slash-commands", "--print-timeout", f"{int(req.timeout_sec)}s",
        ]
        m = safe_model(req.model)
        if m:
            args += ["--model", m]
        if req.json_schema is not None:
            req.log_dir.mkdir(parents=True, exist_ok=True)
            sp = req.log_dir / "schema.json"
            sp.write_text(json.dumps(req.json_schema, ensure_ascii=False), encoding="utf-8")
            args += ["--json-schema", str(sp)]
        return args

    def stdin_for(self, req: CliRequest) -> str:
        web = any(t in ("web_search", "web_fetch") for t in req.tools)
        content = HEADLESS_RULES.format(tools=WEB_TOOLS_RULE if web else NO_TOOLS_RULE) + req.prompt
        return json.dumps({"event": "user", "message": {"content": content}}, ensure_ascii=False) + "\n"

    def make_parser(self) -> StreamParser:
        return AntigravityParser()

    def run(self, req: CliRequest) -> CliResult:
        problem = billing_problem(read_settings())
        if problem:
            return CliResult(self.name, "auth", False, message=problem)
        return super().run(req)

    def auth_status(self) -> dict:
        """Không đọc token (nằm trong Windows Credential Manager); chỉ đọc file cài đặt."""
        s = read_settings()
        _, removed = clean_env(self.name)
        auth_type = ((s or {}).get("auth") or {}).get("type")
        return {
            "installed": bool(self.argv_prefix()),
            "settings_found": s is not None,
            "selected_auth_type": auth_type,
            # agy không có lệnh kiểm tra đăng nhập; trạng thái thật lấy từ lần chạy gần nhất (xem ai.cli_health)
            "logged_in": None,
            "uses_api_key": bool(s) and (s.get("modelProvider") == "gemini" or auth_type in API_AUTH_TYPES),
            "uses_paid_credits": bool(s) and s.get("useG1Credits") is True,
            "billing_problem": billing_problem(s),
            "env_removed": removed,
            "note": "Antigravity CLI không có lệnh kiểm tra đăng nhập — bấm 'Chạy thử thật' để xác minh.",
        }


class AntigravityParser(StreamParser):
    def __init__(self) -> None:
        super().__init__()
        self.deltas: list[str] = []

    def feed(self, line: str):
        d = self._json(line)
        if not d:
            return None
        ev = d.get("event")
        if d.get("conversation_id"):
            self.session_id = d["conversation_id"]
        body = d.get(ev) if isinstance(d.get(ev), dict) else d  # nội dung lồng dưới khóa trùng tên sự kiện
        if body.get("conversation_id"):
            self.session_id = body["conversation_id"]
        if ev == "init":
            self.model = body.get("model")
            return ("init", {"model": self.model, "permission_mode": body.get("permission_mode")})
        if ev == "step_update":
            return self._step(body)
        if ev == "result":
            r = body
            self.final = r
            if r.get("conversation_id"):
                self.session_id = r["conversation_id"]
            return ("result", {"status": r.get("status")})
        return None

    def _step(self, d: dict):
        if d.get("text_delta") and d.get("step_type") == "agent_response":
            self.deltas.append(d["text_delta"])
        info = d.get("tool_info") or {}
        raw = info.get("name") or d.get("tool_name")
        if d.get("step_type") != "tool" or not raw:
            return None
        key = f"s{d.get('step_index')}"
        tc = self.tool_calls.get(key)
        if tc is None:
            tc = self.add_tool(key, canonical_tool(raw), info.get("parameters") if info.get("parameters") is not None else {})
            started = ("tool_use", {"name": raw, "input": json.dumps(info.get("parameters"), ensure_ascii=False)[:300]})
        else:
            started = None
            if info.get("parameters") is not None:
                tc.input = info["parameters"]
        if d.get("state") in ("DONE", "ERROR"):
            err = info.get("error")
            tc.ok = not err and d.get("state") == "DONE"
            out = info.get("output") if not err else (err.get("message") if isinstance(err, dict) else err)
            tc.output_excerpt = (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))[:6000]
            if started is None:
                return ("tool_result", {"name": raw, "ok": tc.ok})
        return started

    def finish(self, exit_code: int | None, stderr: str) -> CliResult:
        res = CliResult("antigravity", "error", False, session_id=self.session_id, observed_model=self.model,
                        tool_calls=self.tools())
        f = self.final or {}
        text = f.get("response")
        res.text = text if isinstance(text, str) else ("".join(self.deltas) if text is None else json.dumps(text, ensure_ascii=False))
        if f.get("structured_output") is not None:
            res.data = f["structured_output"]
        denied = f.get("denied_actions") or []
        if f.get("status") == "SUCCESS" and exit_code == 0 and not (res.text or "").strip() and res.data is None and denied:
            names = ", ".join(sorted({str(a.get("display_name") or a.get("action")) for a in denied if isinstance(a, dict)}))
            res.kind = "tool_denied"
            res.message = (f"Antigravity dừng lượt vì công cụ bị chặn ở chế độ không tương tác ({names}) — "
                           "không có câu trả lời. ROC không cấp quyền chạy lệnh; sẽ thử lại.")
            return res
        if f.get("status") == "SUCCESS" and exit_code == 0:
            res.ok, res.kind = True, "ok"
            if "denied" in stderr.lower():
                res.message = "Một số công cụ bị từ chối (headless): " + stderr.strip()[-300:]
            return res
        err = f.get("error")
        err_s = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False) if err else ""
        blob = " ".join([str(f.get("status") or ""), err_s, stderr[-3000:], "\n".join(self.non_json[-10:])]).strip()
        kind = classify_text(blob)
        if kind == "error" and _AUTH_EXTRA_RE.search(blob):
            kind = "auth"
        if f.get("status") in ("CANCELED", "INTERRUPTED"):
            kind = "cancelled"
        res.kind = kind
        res.quota_reset_at = parse_reset_epoch(blob)
        res.message = blob[-1500:] or f"Antigravity CLI thoát mã {exit_code} mà không trả kết quả"
        return res
