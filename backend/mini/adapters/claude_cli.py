"""Adapter Claude Code (claude -p, stream-json).

Đã kiểm chứng trên Claude Code 2.1.303: --restricted, --strict-mcp-config, --tools,
--permission-mode dontAsk, --permission-prompts none, --json-schema (trả structured_output),
--resume <uuid>, sự kiện rate_limit_event {status, resetsAt}.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .cli_base import (
    CliAdapter, CliRequest, CliResult, StreamParser, classify_text, clean_env,
    parse_reset_epoch, safe_model, safe_session,
)

TOOL_MAP = {"web_search": "WebSearch", "web_fetch": "WebFetch", "read": "Read"}


class ClaudeAdapter(CliAdapter):
    name = "claude"
    exe_name = "claude"

    def build_args(self, req: CliRequest) -> list[str]:
        tools = [TOOL_MAP[t] for t in req.tools if t in TOOL_MAP]
        args = [
            "-p", "--output-format", "stream-json", "--verbose",
            "--restricted", "--strict-mcp-config", "--disable-slash-commands",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", ",".join(tools),
        ]
        if tools:
            args += ["--allowedTools", ",".join(tools)]
        m = safe_model(req.model)
        if m:
            args += ["--model", m]
        if req.json_schema is not None:
            args += ["--json-schema", json.dumps(req.json_schema, ensure_ascii=False)]
        sid = safe_session(req.resume_session_id)
        if sid:
            args += ["--resume", sid]
        return args

    def make_parser(self) -> StreamParser:
        return ClaudeParser()

    def auth_status(self) -> dict:
        pre = self.argv_prefix()
        if not pre:
            return {"installed": False, "logged_in": False}
        env, removed = clean_env(self.name)
        try:
            r = subprocess.run(pre + ["auth", "status", "--json"], capture_output=True, timeout=60, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        except Exception as e:  # noqa: BLE001
            return {"installed": True, "logged_in": False, "error": str(e)}
        method = d.get("authMethod")
        return {
            "installed": True,
            "logged_in": bool(d.get("loggedIn")),
            "auth_method": method,
            "subscription": d.get("subscriptionType"),
            "provider": d.get("apiProvider"),
            "org_name": d.get("orgName"),
            # Mini luôn gỡ biến API key khỏi môi trường của CLI con
            "uses_api_key": method not in (None, "claude.ai"),
            "env_removed": removed,
        }


class ClaudeParser(StreamParser):
    def feed(self, line: str):
        d = self._json(line)
        if not d:
            return None
        t = d.get("type")
        if d.get("session_id"):
            self.session_id = d["session_id"]
        if t == "system" and d.get("subtype") == "init":
            self.model = d.get("model")
            return ("init", {"model": self.model, "tools": d.get("tools")})
        if t == "rate_limit_event":
            info = d.get("rate_limit_info") or {}
            if info.get("status") and info["status"] not in ("allowed", "allowed_warning"):
                self.quota_hit = True
                self.quota_reset = info.get("resetsAt")
            return ("rate_limit", {"status": info.get("status"), "resets_at": info.get("resetsAt")})
        if t == "assistant":
            content = (d.get("message") or {}).get("content") or []
            for c in content:
                if c.get("type") == "tool_use" and c.get("name") != "StructuredOutput":
                    self.add_tool(c.get("id"), c.get("name"), c.get("input"))
                    return ("tool_use", {"name": c.get("name"), "input": _short(c.get("input"))})
                if c.get("type") == "text" and c.get("text"):
                    self.text_parts.append(c["text"])
            return None
        if t == "user":
            content = (d.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_result":
                        tc = self.tool_calls.get(c.get("tool_use_id"))
                        if tc:
                            tc.ok = not c.get("is_error")
                            tc.output_excerpt = _text_of(c.get("content"))[:6000]
                            return ("tool_result", {"name": tc.name, "ok": tc.ok})
            return None
        if t == "result":
            self.final = d
            return ("result", {"is_error": d.get("is_error"), "subtype": d.get("subtype")})
        return None

    def finish(self, exit_code: int | None, stderr: str) -> CliResult:
        res = CliResult("claude", "error", False, session_id=self.session_id, observed_model=self.model,
                        tool_calls=self.tools())
        f = self.final
        if f is None:
            blob = stderr + "\n" + "\n".join(self.non_json[-20:])
            res.kind = "quota" if self.quota_hit else classify_text(blob)
            res.message = (blob.strip() or f"Claude Code thoát mã {exit_code} mà không trả kết quả")[-1500:]
            res.quota_reset_at = self.quota_reset or parse_reset_epoch(blob)
            return res
        text = f.get("result") or ""
        res.text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        res.data = f.get("structured_output")
        res.session_id = f.get("session_id") or self.session_id
        if f.get("modelUsage"):
            res.observed_model = res.observed_model or next(iter(f["modelUsage"]))
        status = f.get("api_error_status")
        if not f.get("is_error") and f.get("subtype") == "success":
            res.ok, res.kind = True, "ok"
            if f.get("permission_denials"):
                res.message = f"Một số công cụ bị từ chối: {[p.get('tool_name') for p in f['permission_denials']]}"
            return res
        blob = " ".join(str(x) for x in (res.text, f.get("errors") or "", f.get("subtype"), stderr[-2000:]))
        res.kind = "quota" if self.quota_hit else classify_text(blob, status if isinstance(status, int) else None)
        if f.get("subtype") == "error_max_turns":
            res.kind = "error"
        res.quota_reset_at = self.quota_reset or parse_reset_epoch(blob)
        res.message = blob.strip()[-1500:]
        return res


def _text_of(c: Any) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_text_of(x.get("text") if isinstance(x, dict) else x) for x in c)
    if isinstance(c, dict):
        return json.dumps(c, ensure_ascii=False)
    return str(c or "")


def _short(v: Any) -> Any:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s[:300]
