"""Kiểm tra video nhạc thật trên file: mở được, giải mã toàn bộ, đúng khung hình, có âm thanh,
không im lặng bất thường, độ dài khớp nhạc, độ lớn gần mục tiêu. Thumbnail đúng kích thước."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from . import ff, render


def _silence(path: Path, noise_db: float = -50, min_sec: float = 2.0) -> list[tuple[float, float]]:
    err = ff.run(["-i", str(path), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"],
                 timeout=render.LONG_TIMEOUT)
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        out.append((max(0.0, s), e if e is not None else -1.0))
    return out


def verify_output(path: Path, *, expected_duration: float, width: int, height: int, target_lufs: float) -> dict:
    """Trả {ok, checks[{name, ok, severity, detail}], duration}. Chỉ lỗi 'error' làm kết quả không đạt."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        checks.append({"name": name, "ok": bool(ok), "severity": severity, "detail": detail})

    if not path.exists() or path.stat().st_size < 10_000:
        add("file", False, "không có file hoặc file quá nhỏ")
        return {"ok": False, "checks": checks, "duration": 0.0}
    info = ff.probe(path)
    v = [s for s in info["streams"] if s["codec_type"] == "video"]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dur = float(info["format"]["duration"])
    add("video_stream", bool(v) and v[0]["width"] == width and v[0]["height"] == height,
        f"{v[0]['codec_name']} {v[0]['width']}x{v[0]['height']}" if v else "thiếu luồng hình")
    add("audio_stream", bool(a), f"{a[0]['codec_name']} {a[0].get('sample_rate', '?')} Hz" if a else "thiếu luồng âm thanh")
    tol = max(1.0, expected_duration * 0.005)
    add("duration", abs(dur - expected_duration) <= tol, f"{dur:.2f}s / nhạc {expected_duration:.2f}s")
    try:
        err = ff.run(["-v", "error", "-i", str(path), "-f", "null", "-"], timeout=render.LONG_TIMEOUT)
        add("decode", not err.strip(), err.strip()[:300] or "giải mã toàn bộ không lỗi")
    except ff.FFmpegError as e:
        add("decode", False, str(e)[:300])
    if a:
        spans = _silence(path)
        total = sum((e if e >= 0 else dur) - s for s, e in spans)
        add("silence", total <= 0.25 * dur,
            f"im lặng {total:.1f}s / {dur:.0f}s" + (f" (đoạn: {', '.join(f'{s:.0f}–{e:.0f}s' for s, e in spans[:5])})"
                                                   if spans else ""))
        loud = render.measure_loudness(path)
        i = loud.get("i")
        add("loudness", i is not None and abs(i - target_lufs) <= 2.0,
            f"{i:.1f} LUFS (mục tiêu {target_lufs:.0f})" if i is not None else "không đo được", severity="warn")
    return {"ok": all(c["ok"] for c in checks if c["severity"] == "error"), "checks": checks, "duration": dur}


def verify_thumbnail(path: Path, size: tuple[int, int] = (1280, 720)) -> dict:
    try:
        with Image.open(path) as im:
            ok = im.size == size
            return {"name": "thumbnail", "ok": ok, "severity": "error", "detail": f"{im.size[0]}x{im.size[1]}"}
    except (OSError, ValueError) as e:
        return {"name": "thumbnail", "ok": False, "severity": "error", "detail": str(e)[:200]}
