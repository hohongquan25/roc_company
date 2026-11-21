"""Bài mẫu: tải âm thanh (yt-dlp) và ĐO bằng chương trình (librosa + FFmpeg).

Chỉ giữ lại con số (BPM, tông, độ dài, độ lớn LUFS, đường năng lượng, độ sáng âm sắc...). File âm thanh
tải về hoặc bạn tải lên được xóa ngay sau khi đo để hạn chế rủi ro bản quyền — ROC không lưu bài của người khác.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path
from urllib.parse import urlparse

from . import ff, render

SR = 22050
PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# hồ sơ tông Krumhansl–Schmuckler
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


class DownloadError(RuntimeError):
    pass


class _QuietLog:
    """yt-dlp: không in ra stderr của worker; lỗi được trả về qua DownloadError."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = debug

    def error(self, msg: str) -> None:
        pass


def check_url(url: str) -> str:
    u = (url or "").strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"Link không hợp lệ (cần http/https): {u[:120]}")
    return u


def download_reference(url: str, out_dir: Path, max_seconds: int) -> tuple[Path, dict]:
    """Tải phần âm thanh của một link bài mẫu. Trả (file, thông tin trang). Lỗi → DownloadError."""
    import yt_dlp

    url = check_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)

    def too_long(info, *, incomplete=False):  # noqa: ARG001 — chữ ký yt-dlp
        d = info.get("duration")
        if d and d > max_seconds:
            return f"bài mẫu dài {int(d)}s, vượt giới hạn {max_seconds}s"
        return None

    opts = {
        "format": "bestaudio/best", "outtmpl": str(out_dir / "ref_%(id)s.%(ext)s"), "noplaylist": True,
        "quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 2,
        "match_filter": too_long, "restrictfilenames": True, "logger": _QuietLog(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("không lấy được thông tin bài mẫu")
            if info.get("_type") == "playlist":
                raise DownloadError("link là danh sách phát — hãy gửi link một bài")
            path = Path(ydl.prepare_filename(info))
    except DownloadError:
        raise
    except Exception as e:  # noqa: BLE001 — yt-dlp ném nhiều loại lỗi khác nhau
        raise DownloadError(str(e).replace("\x1b", "")[:400]) from e
    if not path.exists():
        found = sorted(out_dir.glob("ref_*"))
        if not found:
            raise DownloadError("bị bỏ qua hoặc không có file âm thanh (có thể do vượt giới hạn độ dài)")
        path = found[-1]
    meta = {k: info.get(k) for k in ("title", "uploader", "channel", "artist", "track", "album", "duration",
                                     "webpage_url", "extractor_key")}
    return path, meta


def _decode(src: Path, wav: Path) -> None:
    ff.run(["-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)])


def estimate_key(chroma_mean) -> dict:
    """Tông khả dĩ nhất theo tương quan với hồ sơ Krumhansl–Schmuckler (24 tông)."""
    import numpy as np

    v = np.asarray(chroma_mean, dtype=float)
    best, scores = None, []
    for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
        for k in range(12):
            r = float(np.corrcoef(v, np.roll(prof, k))[0, 1]) if v.std() > 0 else 0.0
            scores.append((r, k, mode))
    scores.sort(reverse=True)
    r, k, mode = scores[0]
    second = scores[1][0]
    best = {"key": PITCHES[k], "mode": mode, "label": f"{PITCHES[k]} {'major' if mode == 'major' else 'minor'}",
            "confidence": round(r, 3), "margin": round(r - second, 3)}
    return best


def _sections(times, db_vals, step: float, total: float) -> list[dict]:
    """Chia bài thành đoạn theo mức năng lượng (thấp / vừa / cao) so với chính bài đó."""
    import numpy as np

    if len(db_vals) < 2:
        return []
    lo, hi = np.percentile(db_vals, 33), np.percentile(db_vals, 66)
    labels = ["thấp" if d < lo else "cao" if d > hi else "vừa" for d in db_vals]
    out: list[dict] = []
    for t, lab in zip(times, labels):
        end = round(min(float(t) + step, total), 1)
        if out and out[-1]["level"] == lab:
            out[-1]["end"] = end
        else:
            out.append({"start": round(float(t), 1), "end": end, "level": lab})
    # gộp đoạn quá ngắn (< 8s) vào đoạn trước
    merged: list[dict] = []
    for s in out:
        if merged and s["end"] - s["start"] < 8:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    return merged


def analyze_file(src: Path, work: Path) -> dict:
    """Đo một file âm thanh. Không sửa file gốc."""
    import librosa
    import numpy as np

    work.mkdir(parents=True, exist_ok=True)
    wav = work / "analysis.wav"
    _decode(src, wav)
    try:
        y, sr = librosa.load(str(wav), sr=SR, mono=True)
    finally:
        wav.unlink(missing_ok=True)
    if len(y) < sr:
        raise ValueError("âm thanh quá ngắn để phân tích (< 1 giây)")
    dur = len(y) / sr
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key = estimate_key(chroma.mean(axis=1))
    rms = librosa.feature.rms(y=y)[0]
    rms_db = librosa.amplitude_to_db(np.maximum(rms, 1e-6), ref=1.0)
    frames_t = librosa.frames_to_time(np.arange(len(rms_db)), sr=sr)
    step = max(1.0, dur / 60)  # tối đa ~60 điểm trên đường năng lượng
    curve_t, curve_db = [], []
    t = 0.0
    while t < dur:
        m = (frames_t >= t) & (frames_t < t + step)
        if m.any():
            curve_t.append(round(t, 1))
            curve_db.append(round(float(rms_db[m].mean()), 1))
        t += step
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    loud = render.measure_loudness(src)
    return {
        "duration_sec": round(dur, 1),
        "tempo_bpm": round(tempo, 1),
        "beats": int(len(beats)),
        "onsets_per_sec": round(len(onsets) / dur, 2),
        "key": key,
        "loudness_lufs": loud.get("i"),
        "true_peak_dbtp": loud.get("tp"),
        "loudness_range_lu": loud.get("lra"),
        "dynamic_range_db": round(float(np.percentile(rms_db, 95) - np.percentile(rms_db, 10)), 1),
        "brightness_hz": round(centroid),
        "energy_curve": [{"t": a, "db": b} for a, b in zip(curve_t, curve_db)],
        "sections": _sections(curve_t, curve_db, step, dur),
    }


def measure_and_forget(src: Path, work: Path) -> dict:
    """Đo rồi XÓA file nguồn và thư mục tạm (bài mẫu không được giữ lại)."""
    try:
        return analyze_file(src, work)
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)


def describe(m: dict) -> str:
    """Một dòng tóm tắt số đo cho báo cáo."""
    k = m.get("key") or {}
    lufs = m.get("loudness_lufs")
    return (f"{m['tempo_bpm']:.0f} BPM · {k.get('label', '?')} (độ tin {k.get('confidence', 0):.2f}) · "
            f"{int(m['duration_sec'] // 60)}:{int(m['duration_sec'] % 60):02d} · "
            f"{'%.1f LUFS' % lufs if isinstance(lufs, (int, float)) and math.isfinite(lufs) else '? LUFS'} · "
            f"độ sáng {m['brightness_hz']} Hz · {m['onsets_per_sec']} nhịp gõ/giây")
