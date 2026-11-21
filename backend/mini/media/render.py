"""Dựng video nhạc ngang 1920x1080 cho YouTube từ file nhạc (Suno) + ảnh nền bạn tải lên.

- Âm thanh: chuẩn hóa độ lớn 2 lượt bằng loudnorm của FFmpeg về mục tiêu (mặc định -14 LUFS).
- Hình: ảnh nền phủ kín khung (cắt giữa) + lớp tối dần phía dưới + tên bài (Pillow) + sóng âm chạy theo nhạc.
- Video dài: nối các bài đã chuẩn hóa, tên bài đổi theo mốc thời gian, kèm danh sách bài để dán vào mô tả.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import get_settings
from . import ff

LONG_TIMEOUT = 6 * 3600


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    s = get_settings()
    for p in ([s.font_bold] if bold else []) + [s.font_regular, r"C:\Windows\Fonts\arial.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# Font dự phòng cho ký tự mà font chính không có (ký hiệu toán, mũi tên, emoji)
_FALLBACK_FILES = [(r"C:\Windows\Fonts\seguisym.ttf", 0), (r"C:\Windows\Fonts\cambria.ttc", 1),
                   (r"C:\Windows\Fonts\seguiemj.ttf", 0)]
_fb_cache: dict[int, list] = {}
_has_cache: dict[tuple, bool] = {}


def _fallbacks(size: int) -> list:
    if size not in _fb_cache:
        fonts = []
        for path, idx in _FALLBACK_FILES:
            try:
                fonts.append(ImageFont.truetype(path, size, index=idx))
            except OSError:
                continue
        _fb_cache[size] = fonts
    return _fb_cache[size]


def _has_glyph(font, ch: str) -> bool:
    key = (getattr(font, "path", ""), font.size, ch)
    if key not in _has_cache:
        if ch.isspace():
            _has_cache[key] = True
        else:
            m, notdef = font.getmask(ch), font.getmask("\U0010FFFD")
            _has_cache[key] = not (m.size == notdef.size and bytes(m) == bytes(notdef))
    return _has_cache[key]


def runs(text: str, font) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for ch in text:
        f = font
        if not _has_glyph(font, ch):
            f = next((fb for fb in _fallbacks(font.size) if _has_glyph(fb, ch)), font)
        if out and out[-1][1] is f:
            out[-1] = (out[-1][0] + ch, f)
        else:
            out.append((ch, f))
    return out


def tlen(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return sum(draw.textlength(s, font=f) for s, f in runs(text, font))


def dtext(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, **kw) -> None:
    x, y = xy
    for s, f in runs(text, font):
        emoji = "seguiemj" in str(getattr(f, "path", ""))
        draw.text((x, y), s, font=f, **({**kw, "embedded_color": True, "stroke_width": 0} if emoji else kw))
        x += draw.textlength(s, font=f)


def _hex(c: str, default=(16, 24, 32)) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (c or "").strip())
    if not m:
        return default
    v = m.group(1)
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if tlen(draw, t, font) <= max_w:
            cur = t
            continue
        if cur:
            lines.append(cur)
        # từ đơn quá dài: cắt theo ký tự
        while tlen(draw, w, font) > max_w and len(w) > 1:
            k = len(w)
            while k > 1 and tlen(draw, w[:k], font) > max_w:
                k -= 1
            lines.append(w[:k])
            w = w[k:]
        cur = w
    if cur:
        lines.append(cur)
    return lines


# --- phụ đề -------------------------------------------------------------------



# --- âm thanh -------------------------------------------------------------------


def _loudnorm_json(err: str) -> dict:
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        raise ff.FFmpegError("không đọc được kết quả đo loudnorm")
    return json.loads(m[-1])


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def measure_loudness(path: Path) -> dict:
    """Độ lớn tích hợp (LUFS), đỉnh thật (dBTP), dải độ lớn (LU) theo EBU R128."""
    err = ff.run(["-i", str(path), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"], timeout=LONG_TIMEOUT)
    d = _loudnorm_json(err)
    return {"i": _num(d.get("input_i")), "tp": _num(d.get("input_tp")), "lra": _num(d.get("input_lra")),
            "thresh": _num(d.get("input_thresh")), "offset": _num(d.get("target_offset"))}


def normalize(src: Path, dst: Path, target: float = -14.0) -> dict:
    """Chuẩn hóa 2 lượt (đo rồi áp tuyến tính) → WAV 48 kHz stereo. Trả số đo trước/sau."""
    before = measure_loudness(src)
    if before["i"] is None:
        raise ff.FFmpegError("file nhạc im lặng hoặc không đo được độ lớn")
    tgt = f"I={target}:TP=-1.0:LRA=11"
    ln = (f"loudnorm={tgt}:measured_I={before['i']}:measured_TP={before['tp'] if before['tp'] is not None else -1}:"
          f"measured_LRA={before['lra'] if before['lra'] is not None else 7}:"
          f"measured_thresh={before['thresh'] if before['thresh'] is not None else -70}:"
          f"offset={before['offset'] if before['offset'] is not None else 0}:linear=true:print_format=json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    ff.run(["-i", str(src), "-vn", "-af", ln, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dst)],
           timeout=LONG_TIMEOUT)
    after = measure_loudness(dst)
    return {"before": before, "after": after, "duration": ff.duration(dst)}


def concat_audio(parts: list[Path], out: Path) -> Path:
    """Nối các WAV cùng định dạng (48 kHz stereo PCM) thành một file."""
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    ff.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], timeout=LONG_TIMEOUT)
    lst.unlink(missing_ok=True)
    return out


# --- hình -----------------------------------------------------------------------


def cover(image: Path, size: tuple[int, int]) -> Image.Image:
    w, h = size
    im = Image.open(image).convert("RGB")
    sc = max(w / im.width, h / im.height)
    im = im.resize((math.ceil(im.width * sc), math.ceil(im.height * sc)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def background(image: Path, out: Path, size: tuple[int, int]) -> Path:
    """Ảnh nền phủ kín khung + lớp tối dần ở 45% dưới để chữ và sóng âm dễ đọc."""
    w, h = size
    im = cover(image, size).convert("RGBA")
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    top = int(h * 0.55)
    for y in range(top, h):
        a = int(170 * (y - top) / max(1, h - top))
        d.line([(0, y), (w, y)], fill=(0, 0, 0, a))
    Image.alpha_composite(im, shade).convert("RGB").save(out)
    return out


def title_layer(title: str, sub: str, out: Path, size: tuple[int, int]) -> Path:
    """Lớp chữ trong suốt: tên bài lớn + dòng phụ, góc dưới trái (trên vùng sóng âm)."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(True, max(36, h // 15))
    lines = wrap(d, title, f, int(w * 0.8))[:2]
    fs = _font(False, max(22, h // 34))
    y = int(h * 0.58)
    for ln in lines:
        dtext(d, (int(w * 0.05), y), ln, font=f, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 200))
        y += f.size + 10
    if sub:
        dtext(d, (int(w * 0.05), y + 4), sub, font=fs, fill=(230, 230, 230, 235), stroke_width=2, stroke_fill=(0, 0, 0, 180))
    img.save(out)
    return out


# --- video ----------------------------------------------------------------------


def _waves(h: int) -> int:
    return max(40, (h // 5) // 2 * 2)


def _video_args(fps: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart"]


def _wave_filter(w: int, h: int, fps: int) -> str:
    wh = _waves(h)
    return (f"[1:a]showwaves=s={w}x{wh}:mode=cline:rate={fps}:colors=white:draw=full:scale=sqrt,format=rgba,"
            f"colorchannelmixer=aa=0.75[wv];[0:v][wv]overlay=0:{h - wh - h // 20}")


def render_track_video(*, image: Path, audio: Path, title: str, sub: str, out: Path,
                       size: tuple[int, int] | None = None, fps: int | None = None) -> dict:
    """Một bài một video: nền + tên bài + sóng âm."""
    s = get_settings()
    w, h = size or (s.video_width, s.video_height)
    fps = fps or s.video_fps
    work = out.parent / f"{out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    bg = background(image, work / "bg.png", (w, h))
    tl = title_layer(title, sub, work / "title.png", (w, h))
    dur = ff.duration(audio)
    fl = f"{_wave_filter(w, h, fps)}[b];[b][2:v]overlay=0:0,format=yuv420p[v]"
    ff.run(["-loop", "1", "-framerate", str(fps), "-i", str(bg), "-i", str(audio), "-i", str(tl),
            "-filter_complex", fl, "-map", "[v]", "-map", "1:a", "-t", f"{dur:.3f}", *_video_args(fps), str(out)],
           timeout=LONG_TIMEOUT)
    return {"path": str(out), "duration": dur, "width": w, "height": h}


def fmt_ts(sec: float) -> str:
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_long_video(*, image: Path, parts: list[tuple[Path, str]], heading: str, out: Path,
                      size: tuple[int, int] | None = None, fps: int | None = None) -> dict:
    """Video dài: nối các bài, tên bài đổi đúng mốc thời gian; trả danh sách bài kèm mốc."""
    s = get_settings()
    w, h = size or (s.video_width, s.video_height)
    fps = fps or s.video_fps
    work = out.parent / f"{out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    audio = concat_audio([p for p, _ in parts], work / "long.wav")
    bg = background(image, work / "bg.png", (w, h))
    chapters, t = [], 0.0
    for p, title in parts:
        d = ff.duration(p)
        chapters.append({"start": round(t, 3), "end": round(t + d, 3), "title": title, "ts": fmt_ts(t)})
        t += d
    args = ["-loop", "1", "-framerate", str(fps), "-i", str(bg), "-i", str(audio)]
    fl = [f"{_wave_filter(w, h, fps)}[b0]"]
    last = "b0"
    for i, c in enumerate(chapters):
        png = title_layer(c["title"], f"{heading} · {i + 1}/{len(chapters)}", work / f"title_{i:02d}.png", (w, h))
        args += ["-i", str(png)]
        nxt = f"b{i + 1}"
        fl.append(f"[{last}][{i + 2}:v]overlay=0:0:enable='between(t,{c['start']:.3f},{c['end']:.3f})'[{nxt}]")
        last = nxt
    fl.append(f"[{last}]format=yuv420p[v]")
    ff.run([*args, "-filter_complex", ";".join(fl), "-map", "[v]", "-map", "1:a", "-t", f"{t:.3f}",
            *_video_args(fps), str(out)], timeout=LONG_TIMEOUT)
    tracklist = "\n".join(f"{c['ts']} {c['title']}" for c in chapters)
    return {"path": str(out), "duration": t, "width": w, "height": h, "chapters": chapters, "tracklist": tracklist}


def thumbnail(template: dict, title: str, out: Path, size=(1280, 720), image: Path | None = None) -> Path:
    w, h = size
    bg, ac = _hex(template.get("bg"), (16, 24, 32)), _hex(template.get("accent"), (242, 170, 76))
    img = Image.new("RGB", size, bg)
    if image and image.exists():
        im = Image.open(image).convert("RGB")
        sc = max(w / im.width, h / im.height)
        im = im.resize((math.ceil(im.width * sc), math.ceil(im.height * sc)))
        img.paste(im.crop(((im.width - w) // 2, (im.height - h) // 2, (im.width - w) // 2 + w, (im.height - h) // 2 + h)))
        img = Image.blend(img, Image.new("RGB", size, (0, 0, 0)), 0.45)
    d = ImageDraw.Draw(img)
    d.rectangle([0, h - 24, w, h], fill=ac)
    f = _font(True, int(template.get("font_size", 96 if w > h else 110)))
    text = title.upper() if template.get("uppercase", True) else title
    lines = wrap(d, text, f, w - 140)
    while len(lines) > 3 and f.size > 48:
        f = _font(True, f.size - 8)
        lines = wrap(d, text, f, w - 140)
    y = (h - len(lines) * (f.size + 14)) / 2
    for l in lines[:4]:
        dtext(d, (70, y), l, font=f, fill=(255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0))
        y += f.size + 14
    img.save(out)
    return out
