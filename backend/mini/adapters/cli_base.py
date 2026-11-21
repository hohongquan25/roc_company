"""Phần chung cho adapter Claude Code / Antigravity CLI.

Nguyên tắc an toàn:
- Gọi trực tiếp file thực thi (claude.exe, agy.exe), không qua cmd.exe/shell.
- Prompt và mọi nội dung không tin cậy chỉ đi qua stdin, không bao giờ vào dòng lệnh.
- Môi trường con bị gỡ các biến có thể chuyển CLI sang API trả phí.
- Không bật chế độ bỏ qua kiểm soát quyền.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..events import redact
from ..jsonutil import InvalidOutput, extract_json, validate

# Biến khiến CLI dùng API key/nhà cung cấp trả phí thay vì đăng nhập tài khoản.
BILLING_ENV = {
    "claude": [
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "AWS_BEARER_TOKEN_BEDROCK",
    ],
    "antigravity": [
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH", "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_APPLICATION_CREDENTIALS",
    ],
}
# Biến do một phiên Claude Code cha để lại (khi Mini được khởi chạy từ trong Claude Code).
_PARENT_SESSION_ENV = re.compile(r"^(CLAUDECODE|CLAUDE_PID|CLAUDE_CODE_(SESSION_ID|CHILD_SESSION|MESSAGING_.*|ENTRYPOINT|SESSION_ATTENDED|EXECPATH|SDK_.*|ENABLE_SDK_.*|EMIT_.*|STARTUP_.*|QUESTION_.*|ENABLE_TASKS)|CLAUDE_AGENT_SDK_VERSION|CLAUDE_EFFORT)$")

ERROR_KINDS = (
    "ok", "auth", "quota", "network", "invalid_output", "timeout", "cancelled",
    "not_installed", "tool_denied", "error",
)

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-\[\]]{0,80}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def safe_model(model: str | None) -> str | None:
    if not model:
        return None
    if not _MODEL_RE.match(model):
        raise ValueError(f"tên model không hợp lệ: {model!r}")
    return model


def safe_session(sid: str | None) -> str | None:
    if not sid:
        return None
    if not _UUID_RE.match(sid):
        raise ValueError("session id không hợp lệ")
    return sid


def clean_env(cli: str) -> tuple[dict, list[str]]:
    env = dict(os.environ)
    removed = []
    for k in BILLING_ENV.get(cli, []):
        if k in env:
            env.pop(k)
            removed.append(k)
    for k in list(env):
        if _PARENT_SESSION_ENV.match(k):
            env.pop(k)
    if cli == "antigravity":
        env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"  # tránh trình cập nhật nền chen vào lần chạy headless
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env, removed


def resolve_executable(name: str, override: str | None = None) -> list[str] | None:
    """Trả về argv tiền tố để chạy CLI không qua cmd.exe."""
    if override:
        p = Path(override)
        if not p.exists():
            return None
        if p.suffix in (".js", ".mjs", ".cjs"):
            return [shutil.which("node") or "node", str(p)]
        if p.suffix == ".py":  # CLI giả lập dùng trong kiểm thử (được ghi nhãn mock)
            return [sys.executable, str(p)]
        return [str(p)]
    found = shutil.which(name)
    if not found:
        return None
    p = Path(found)
    if p.suffix.lower() in (".cmd", ".bat", ".ps1"):
        cmd = p.with_suffix(".cmd")
        if cmd.exists():
            text = cmd.read_text(encoding="utf-8", errors="ignore")
            m = re.findall(r'"%dp0%\\([^"]+\.(?:exe|js|cjs|mjs))"', text)
            if m:
                target = (p.parent / m[-1]).resolve()
                if target.suffix == ".exe":
                    return [str(target)]
                node = shutil.which("node") or "node"
                return [node, str(target)]
        return None
    return [str(p)]


@dataclass
class ToolCall:
    id: str | None
    name: str
    input: Any
    ok: bool | None = None
    output_excerpt: str = ""


@dataclass
class CliRequest:
    prompt: str
    cwd: Path
    log_dir: Path
    model: str | None = None
    tools: list[str] = field(default_factory=list)  # tên logic: web_search, web_fetch, read
    json_schema: dict | None = None
    resume_session_id: str | None = None
    timeout_sec: int = 900
    cancel_check: Callable[[], bool] | None = None
    on_event: Callable[[str, dict], None] | None = None
    on_start: Callable[[int], None] | None = None


@dataclass
class CliResult:
    cli: str
    kind: str
    ok: bool
    text: str = ""
    data: Any = None
    session_id: str | None = None
    exit_code: int | None = None
    observed_model: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    quota_reset_at: float | None = None
    message: str = ""
    stdout_path: str | None = None
    stderr_path: str | None = None
    args: list[str] = field(default_factory=list)
    removed_env: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cli": self.cli, "kind": self.kind, "ok": self.ok, "text": self.text[:20000],
            "data": self.data, "session_id": self.session_id, "exit_code": self.exit_code,
            "observed_model": self.observed_model, "quota_reset_at": self.quota_reset_at,
            "message": self.message,
            "tool_calls": [tc.__dict__ for tc in self.tool_calls],
        }


# --- phân loại lỗi ------------------------------------------------------------

_QUOTA_RE = re.compile(
    r"usage limit|rate.?limit|hit your limit|limit reached|quota|RESOURCE_EXHAUSTED|"
    r"exhausted your|too many requests|\b429\b|TerminalQuotaError|RetryableQuotaError|out of (extra )?usage",
    re.I,
)
_AUTH_RE = re.compile(
    r"not logged in|please run /login|/login|invalid api key|authentication|unauthori[sz]ed|"
    r"\b401\b|oauth token has expired|credentials|Please set an Auth method|FatalAuthenticationError|sign in",
    re.I,
)
_NET_RE = re.compile(
    r"ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|fetch failed|network|socket hang up|"
    r"Connection error|getaddrinfo|\b50[234]\b|\b529\b|overloaded|Service Unavailable",
    re.I,
)
_RESET_EPOCH_RE = re.compile(r"\|(\d{10})\b")


def classify_text(text: str, status: int | None = None) -> str:
    if status == 429 or (text and _QUOTA_RE.search(text)):
        return "quota"
    if status in (401, 403) or (text and _AUTH_RE.search(text)):
        return "auth"
    if (status and status >= 500) or (text and _NET_RE.search(text)):
        return "network"
    return "error"


def parse_reset_epoch(text: str) -> float | None:
    m = _RESET_EPOCH_RE.search(text or "")
    return float(m.group(1)) if m else None


# --- chạy tiến trình ----------------------------------------------------------


def kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:  # pragma: no cover
        try:
            os.killpg(pid, 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass


@dataclass
class ProcOutcome:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout_path: Path
    stderr_path: Path
    duration: float


def run_process(
    argv: list[str], *, stdin_text: str, cwd: Path, env: dict, log_dir: Path,
    timeout_sec: int, cancel_check: Callable[[], bool] | None,
    on_line: Callable[[str], None] | None, on_start: Callable[[int], None] | None = None,
) -> ProcOutcome:
    log_dir.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    out_p, err_p = log_dir / "stdout.jsonl", log_dir / "stderr.txt"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.time()
    with open(out_p, "w", encoding="utf-8") as out_f, open(err_p, "w", encoding="utf-8") as err_f:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=err_f, creationflags=flags, start_new_session=(sys.platform != "win32"),
        )
        if on_start:
            on_start(proc.pid)

        def feed() -> None:
            try:
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def pump() -> None:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                out_f.write(line)
                out_f.flush()
                if on_line:
                    try:
                        on_line(line.rstrip("\r\n"))
                    except Exception:
                        pass

        tf = threading.Thread(target=feed, daemon=True)
        tp = threading.Thread(target=pump, daemon=True)
        tf.start()
        tp.start()
        timed_out = cancelled = False
        while proc.poll() is None:
            if time.time() - started > timeout_sec:
                timed_out = True
                kill_tree(proc.pid)
                break
            if cancel_check and cancel_check():
                cancelled = True
                kill_tree(proc.pid)
                break
            time.sleep(0.25)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            proc.wait(timeout=15)
        tp.join(timeout=10)
    return ProcOutcome(proc.returncode, timed_out, cancelled, out_p, err_p, time.time() - started)


class CliAdapter:
    name = "base"
    exe_name = "base"

    def __init__(self, override_path: str | None = None):
        self.override_path = override_path

    def argv_prefix(self) -> list[str] | None:
        return resolve_executable(self.exe_name, self.override_path)

    def version(self) -> dict:
        pre = self.argv_prefix()
        if not pre:
            return {"installed": False, "version": None}
        env, _ = clean_env(self.name)
        try:
            r = subprocess.run(pre + ["--version"], capture_output=True, timeout=60, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"installed": True, "version": r.stdout.decode("utf-8", "replace").strip(), "path": pre[-1]}
        except Exception as e:  # noqa: BLE001
            return {"installed": True, "version": None, "error": str(e)}

    # cần lớp con hiện thực
    def build_args(self, req: CliRequest) -> list[str]:
        raise NotImplementedError

    def make_parser(self) -> "StreamParser":
        raise NotImplementedError

    def stdin_for(self, req: CliRequest) -> str:
        return req.prompt

    def run(self, req: CliRequest) -> CliResult:
        pre = self.argv_prefix()
        if not pre:
            return CliResult(self.name, "not_installed", False, message=f"Chưa cài {self.exe_name}")
        try:
            args = self.build_args(req)
        except ValueError as e:
            return CliResult(self.name, "error", False, message=str(e))
        env, removed = clean_env(self.name)
        parser = self.make_parser()

        def on_line(line: str) -> None:
            ev = parser.feed(line)
            if ev and req.on_event:
                req.on_event(*ev)

        oc = run_process(
            pre + args, stdin_text=self.stdin_for(req), cwd=req.cwd, env=env, log_dir=req.log_dir,
            timeout_sec=req.timeout_sec, cancel_check=req.cancel_check, on_line=on_line,
            on_start=req.on_start,
        )
        stderr = oc.stderr_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        res = parser.finish(oc.exit_code, stderr)
        res.cli = self.name
        res.exit_code = oc.exit_code
        res.stdout_path, res.stderr_path = str(oc.stdout_path), str(oc.stderr_path)
        res.args = args
        res.removed_env = removed
        if oc.timed_out:
            res.kind, res.ok, res.message = "timeout", False, f"Quá thời gian {req.timeout_sec}s, đã dừng tiến trình"
        elif oc.cancelled:
            res.kind, res.ok, res.message = "cancelled", False, "Đã hủy theo yêu cầu"
        if res.ok and req.json_schema is not None:
            try:
                if res.data is None:
                    res.data = extract_json(res.text)
                validate(res.data, req.json_schema)
            except InvalidOutput as e:
                res.ok, res.kind, res.message = False, "invalid_output", f"Kết quả không hợp lệ: {e}"
        res.message = redact(res.message)
        return res


class StreamParser:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.tool_calls: dict[str, ToolCall] = {}
        self.order: list[str] = []
        self.text_parts: list[str] = []
        self.final: dict | None = None
        self.quota_reset: float | None = None
        self.quota_hit = False
        self.non_json: list[str] = []
        self.warnings: list[str] = []

    def _json(self, line: str) -> dict | None:
        if not line.strip():
            return None
        try:
            d = json.loads(line)
            return d if isinstance(d, dict) else None
        except json.JSONDecodeError:
            self.non_json.append(line)
            return None

    def add_tool(self, tid: str | None, name: str, inp: Any) -> ToolCall:
        key = tid or f"anon{len(self.order)}"
        tc = ToolCall(tid, name, inp)
        self.tool_calls[key] = tc
        self.order.append(key)
        return tc

    def tools(self) -> list[ToolCall]:
        return [self.tool_calls[k] for k in self.order]

    def feed(self, line: str) -> tuple[str, dict] | None:
        raise NotImplementedError

    def finish(self, exit_code: int | None, stderr: str) -> CliResult:
        raise NotImplementedError
