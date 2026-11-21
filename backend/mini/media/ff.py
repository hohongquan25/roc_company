"""Gọi FFmpeg/ffprobe bằng danh sách đối số (không qua shell)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import get_settings

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], timeout: int = 1800) -> str:
    s = get_settings()
    r = subprocess.run([s.ffmpeg, "-hide_banner", "-y", *args], capture_output=True, timeout=timeout,
                       creationflags=_NOWIN)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise FFmpegError(err[-2000:])
    return err


def probe(path: Path) -> dict:
    s = get_settings()
    r = subprocess.run([s.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, timeout=120, creationflags=_NOWIN)
    if r.returncode != 0:
        raise FFmpegError(r.stderr.decode("utf-8", "replace")[-1000:])
    return json.loads(r.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def available() -> dict:
    s = get_settings()
    try:
        r = subprocess.run([s.ffmpeg, "-version"], capture_output=True, timeout=30, creationflags=_NOWIN)
        return {"installed": r.returncode == 0, "version": r.stdout.decode().splitlines()[0]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"installed": False, "error": str(e)}
