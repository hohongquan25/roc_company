from __future__ import annotations

from pathlib import Path

from PIL import Image

from mini import db
from mini.media import ff
from mini.orchestrator import queue, reqs, worker

W = "test-worker"


def run_next(kind: str | None = None) -> tuple[str, dict]:
    """Nhận và chạy đúng một việc đến hạn (tùy chọn lọc theo loại)."""
    if kind:
        others = {r["kind"] for r in db.qa("SELECT DISTINCT kind FROM jobs WHERE status='queued'")} - {kind}
        job = queue.claim(W, 120, kinds_blocked=others)
    else:
        job = queue.claim(W, 120)
    assert job, f"không có việc {kind or ''} đến hạn"
    st = worker.execute_job(job, W)
    return st, db.q1("SELECT * FROM jobs WHERE id=?", job["id"])


def job_of(kind: str, request_id: int | None = None) -> dict:
    if request_id:
        return db.q1("SELECT * FROM jobs WHERE kind=? AND request_id=? ORDER BY id DESC LIMIT 1", kind, request_id)
    return db.q1("SELECT * FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1", kind)


def stage(rid: int) -> str:
    return db.q1("SELECT stage FROM requests WHERE id=?", rid)["stage"]


def new_request(env, **kw) -> dict:
    env.team()
    body = {"genre": "lofi chill piano", "note": "nghe khi học bài", "output_type": "both", "track_count": 2,
            "vocals": "instrumental", "links": [], **kw}
    return reqs.create(body)


# --- media giả lập bằng FFmpeg/Pillow thật (không cần mạng) -------------------------------------


def make_tone(path: Path, sec: float = 12, freq: int = 440, bpm: int = 120, volume: float = 0.3) -> Path:
    """Âm A4 nền + tiếng gõ đều theo BPM — đủ để đo nhịp và tông."""
    period = 60 / bpm
    expr = (f"{volume}*(sin(2*PI*{freq}*t)+2.6*sin(2*PI*1200*t)*lt(mod(t\\,{period:.4f})\\,0.04))")
    ff.run(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={sec}", "-ac", "2", str(path)])
    return path


def make_image(path: Path, size=(800, 450), color=(40, 80, 140)) -> Path:
    im = Image.new("RGB", size, color)
    for x in range(0, size[0], 40):
        for y in range(0, size[1], 40):
            if (x + y) // 40 % 2:
                im.putpixel((x, y), (255, 255, 255))
    im.save(path)
    return path


# --- đầu ra AI giả lập (MOCK) -----------------------------------------------------------------

LEAD = {"summary": "Làm 2 bài lofi piano không lời, ghép video dài và video từng bài.",
        "assignments": [{"who": "Lan", "task": "đo và phân tích bài mẫu"}, {"who": "Hùng", "task": "viết 2 prompt Suno"},
                        {"who": "Mai", "task": "soát prompt và video"}, {"who": "Tuấn", "task": "dựng video"}],
        "risks": ["không chép giai điệu bài mẫu"]}
RESEARCH = {"summary": "Nhịp vừa phải khoảng 120 BPM, tông A, cảm giác thư giãn, piano mộc và trống nhẹ.",
            "tempo": "~120 BPM (số đo)", "key_mode": "A major (số đo, độ tin trung bình)",
            "instruments": ["piano", "trống lo-fi", "bass ấm"], "mood": ["thư giãn", "ấm áp"],
            "structure": ["intro ngắn", "lặp chủ đề", "outro nhỏ dần"], "production_notes": ["tiếng vinyl nhẹ"],
            "recommendations": ["giữ BPM 70–90 cho lofi", "piano chủ đạo"]}


def write_out(n: int = 2, style: str | None = None, lyrics: str = "") -> dict:
    return {"tracks": [{"title": f"Mưa đêm số {i + 1}", "style": style or
                        f"lofi hip hop, mellow piano lead, soft boom bap drums, warm bass, vinyl crackle, 75 bpm, "
                        f"relaxing study mood, variation {i + 1}", "lyrics": lyrics} for i in range(n)],
            "image_prompts": ["căn phòng ấm áp bên cửa sổ mưa đêm, ánh đèn vàng", "góc bàn học với cây xanh"],
            "youtube": {"title": "Lofi mưa đêm — nhạc học bài", "description": "Nhạc lofi thư giãn để học và làm việc.",
                        "tags": ["lofi", "study", "piano"]},
            "tracklist_template": "[00:00] Mưa đêm số 1"}


def outline_out(n: int = 2) -> dict:
    w = write_out(n)
    return {"tracks": [{"title": t["title"], "concept": f"piano chủ đạo, biến tấu {i + 1}"} for i, t in enumerate(w["tracks"])],
            "image_prompts": w["image_prompts"], "youtube": w["youtube"], "tracklist_template": w["tracklist_template"]}


def track_out(i: int, style: str | None = None, lyrics: str = "") -> dict:
    return write_out(i + 1, style=style, lyrics=lyrics)["tracks"][i]


LEAD_PLAN = LEAD
LEAD = {**LEAD_PLAN, **outline_out(2)}  # Minh lập kế hoạch kèm dàn bài
BRIEF = {"direction": "Piano mộc dẫn dắt, trống nhẹ, năng lượng thấp, khác bài kia ở nhạc cụ chính.",
         "tempo": "75 BPM", "key": "A major", "instruments": ["piano", "trống lo-fi"]}


CHECK_PASS = {"verdict": "pass", "summary": "Prompt rõ ràng, không có tên nghệ sĩ.", "issues": []}
CHECK_REVISE = {"verdict": "revise", "summary": "Prompt bài 2 chưa khác bài 1.",
                "issues": [{"track": 1, "reason": "Bài 2 gần như trùng bài 1, đổi nhạc cụ chính"}]}
SUMMARY = {"headline": "Hoàn thành 2 bài lofi", "body": "- 2 video từng bài + 1 video dài\n- Nhớ bật nhãn nội dung AI khi đăng"}
