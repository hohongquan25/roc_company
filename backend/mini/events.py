"""Nhật ký sự kiện thật — nguồn cho giao diện (SSE) và báo cáo."""
from __future__ import annotations

import re
from typing import Any

from . import db

# Không bao giờ ghi token/khóa vào nhật ký, kể cả khi lỗi trả về chứa chúng.
_SECRET_PATTERNS = [
    re.compile(r"(ya29\.[0-9A-Za-z_\-]+)"),
    re.compile(r"(1//[0-9A-Za-z_\-]{20,})"),
    re.compile(r"(sk-ant-[0-9A-Za-z_\-]+)"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{30,})"),
    re.compile(r'("?(access_token|refresh_token|client_secret|id_token)"?\s*[:=]\s*"?)[^",\s}]+', re.I),
    re.compile(r"(Bearer\s+)[0-9A-Za-z._\-]+", re.I),
]


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    out = text
    for p in _SECRET_PATTERNS:
        if p.groups >= 2:
            out = p.sub(lambda m: m.group(1) + "***", out)
        else:
            out = p.sub("***", out)
    return out


def emit(
    type: str,
    message: str,
    *,
    team_id: int | None = None,
    job_id: int | None = None,
    request_id: int | None = None,
    level: str = "info",
    data: Any = None,
) -> int:
    return db.execute(
        "INSERT INTO events(ts, team_id, job_id, request_id, level, type, message, data) VALUES(?,?,?,?,?,?,?,?)",
        db.now(), team_id, job_id, request_id, level, type, redact(message),
        redact(db.jdump(data)) if data is not None else None,
    )
