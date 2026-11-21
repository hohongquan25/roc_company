"""Yêu cầu của giám đốc: tạo, chuyển bước, báo cáo trên dòng thời gian, thao tác ở các cổng chờ bạn.

Luồng một yêu cầu:
  lead (Minh lập kế hoạch) → research (Lan đo + phân tích bài mẫu) → writing (Hùng viết prompt Suno)
  → checking (Mai soát; cần sửa thì quay lại Hùng, có giới hạn vòng) → awaiting_prompt_approval (BẠN duyệt)
  → awaiting_uploads (BẠN tạo nhạc trên Suno, tải nhạc + ảnh lên, bấm Bắt đầu dựng)
  → producing (Tuấn: chuẩn hóa âm thanh, dựng video, thumbnail) → verifying (Mai kiểm tra file thật)
  → awaiting_final (BẠN duyệt video) → summary (Thảo viết tổng kết) → done
"""
from __future__ import annotations

import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from .. import db
from ..events import emit
from . import queue, roles, teams

STAGE_VI = {
    "lead": "Trưởng nhóm lập kế hoạch",
    "research": "Nghiên cứu bài mẫu",
    "awaiting_reference": "Chờ bạn tải file bài mẫu",
    "writing": "Viết prompt Suno",
    "checking": "Kiểm tra prompt",
    "awaiting_prompt_approval": "Chờ bạn duyệt prompt",
    "awaiting_uploads": "Chờ bạn tạo nhạc trên Suno & tải lên",
    "producing": "Dựng video",
    "verifying": "Kiểm tra video",
    "awaiting_final": "Chờ bạn duyệt video",
    "summary": "Trợ lý viết tổng kết",
    "done": "Hoàn thành",
    "needs_attention": "Cần bạn xử lý",
    "cancelled": "Đã hủy",
}
# bước dừng chờ BẠN làm gì đó → hiện ở "Cần bạn xử lý" và khay bàn giám đốc
GATES = {
    "awaiting_reference": "Không tải được bài mẫu — tải file âm thanh bài mẫu lên để Lan phân tích",
    "awaiting_prompt_approval": "Mai chưa cho đạt một số bài sau các vòng sửa — xem rồi duyệt (hoàn thành) hoặc yêu cầu sửa",
    "awaiting_uploads": "Tạo nhạc trên Suno bằng các prompt đã duyệt, tải file nhạc + ảnh nền lên, rồi bấm Bắt đầu dựng",
    "awaiting_final": "Xem video thành phẩm rồi duyệt hoặc yêu cầu làm lại",
    "needs_attention": "Một bước gặp sự cố — đọc báo cáo mới nhất và chọn cách xử lý",
}
UPLOAD_STAGES = ("awaiting_uploads", "awaiting_final", "needs_attention")
OUTPUT_VI = {"single": "Mỗi bài một video", "long": "Video dài ghép nhiều bài", "both": "Cả hai"}
VOCALS_VI = {"vocal": "Có lời", "instrumental": "Không lời"}
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def get(rid: int) -> dict:
    r = db.q1("SELECT * FROM requests WHERE id=?", rid)
    if not r:
        raise KeyError("không có yêu cầu này")
    return r


def rdir(r: dict) -> Path:
    t = teams.get_team(r["team_id"])
    d = teams.team_dir(t) / "requests" / str(r["id"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def jget(r: dict, key: str, default: Any = None) -> Any:
    return db.jload(r.get(key), default)


def set_stage(rid: int, stage: str, note: str | None = None) -> None:
    status = {"done": "done", "cancelled": "cancelled"}.get(stage, "gate" if stage in GATES else "active")
    db.execute("UPDATE requests SET stage=?, status=?, stage_note=?, updated_at=? WHERE id=?",
               stage, status, note, db.now(), rid)
    level = "warn" if stage in GATES else "info"
    emit("request.stage", f"Yêu cầu #{rid}: {STAGE_VI.get(stage, stage)}" + (f" — {note}" if note else ""),
         team_id=teams.WORKSPACE_ID, request_id=rid, level=level, data={"stage": stage})


def author_of(role: str | None) -> str:
    if role is None:
        return "Bạn"
    e = db.q1("SELECT name FROM employees WHERE role=?", role)
    vi = roles.ROLES.get(role, "Trợ lý điều hành" if role == roles.EXEC_ROLE else role)
    return f"{e['name']} · {vi}" if e else vi


def add_report(rid: int, step: str, role: str | None, title: str, body: str, data: Any = None,
               job_id: int | None = None) -> int:
    rep = db.execute(
        "INSERT INTO request_reports(request_id, step, role, author, title, body, data, job_id, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)", rid, step, role, author_of(role), title, body,
        db.jdump(data) if data is not None else None, job_id, db.now())
    emit("request.report", f"Báo cáo mới ({author_of(role)}): {title}", team_id=teams.WORKSPACE_ID,
         request_id=rid, job_id=job_id, data={"step": step, "role": role})
    return rep


def enqueue(r: dict, kind: str, payload: dict | None = None, key: str | None = None, priority: int = 100) -> int | None:
    from .pipeline import ROLE_OF
    return queue.enqueue(kind, team_id=r["team_id"], request_id=r["id"], role=ROLE_OF.get(kind), payload=payload,
                         idempotency_key=f"{kind}:r{r['id']}:{key or uuid.uuid4().hex[:10]}", priority=priority)


# --- tạo yêu cầu -----------------------------------------------------------------------------


def _title_of(genre: str, links: list[str]) -> str:
    return (genre.strip()[:60] or "Yêu cầu nhạc") + (f" · {len(links)} bài mẫu" if links else "")


def create(body: dict) -> dict:
    from ..media.analysis import check_url
    t = teams.ensure_workspace()
    raw = body.get("links") or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    links = [check_url(x) for x in (s.strip() for s in raw) if x]
    if len(links) > 5:
        raise ValueError("Tối đa 5 link bài mẫu cho một yêu cầu")
    genre = " ".join(str(body.get("genre") or "").split())
    if len(genre) < 2:
        raise ValueError("Cần ghi thể loại (vd: lofi chill, ballad buồn, EDM sôi động)")
    out_type = body.get("output_type") or "single"
    if out_type not in OUTPUT_VI:
        raise ValueError("Kiểu video không hợp lệ")
    try:
        n = int(body["track_count"]) if body.get("track_count") not in (None, "") else 1
    except (TypeError, ValueError):
        raise ValueError("Số bài phải là số")
    if not 1 <= n <= 30:
        raise ValueError("Số bài từ 1 đến 30")
    vocals = body.get("vocals") or "instrumental"
    if vocals not in VOCALS_VI:
        raise ValueError("Chọn có lời hay không lời")
    lang = str(body.get("lyrics_language") or "vi")[:20]
    title = " ".join(str(body.get("title") or "").split())[:100] or _title_of(genre, links)
    ts = db.now()
    rid = db.execute(
        "INSERT INTO requests(team_id, title, links, genre, note, output_type, track_count, vocals, lyrics_language, "
        "stage, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        t["id"], title, db.jdump(links), genre, str(body.get("note") or "").strip()[:3000], out_type, n, vocals, lang,
        "lead", "active", ts, ts)
    r = get(rid)
    add_report(rid, "request", None, "Bạn gửi yêu cầu", request_brief_md(r))
    emit("request.created", f"Yêu cầu mới #{rid}: {title}", team_id=t["id"], request_id=rid)
    enqueue(r, "lead_plan", key="start")
    enqueue(r, "research", key="start")  # Lan đo bài mẫu song song với Minh
    return r


def request_brief_md(r: dict) -> str:
    links = jget(r, "links", [])
    lines = [f"- **Thể loại:** {r['genre']}",
             f"- **Kiểu video:** {OUTPUT_VI[r['output_type']]} · **Số bài:** {r['track_count']} · "
             f"**{VOCALS_VI[r['vocals']]}**" + (f" (lời: {r['lyrics_language']})" if r["vocals"] == "vocal" else "")]
    if links:
        lines.append("- **Bài mẫu:**")
        lines += [f"  - {u}" for u in links]
    else:
        lines.append("- **Bài mẫu:** không có — làm theo thể loại")
    if r["note"]:
        lines += ["", "**Ghi chú của bạn:**", "", r["note"]]
    return "\n".join(lines)


# --- tên phải tránh (nghệ sĩ/bài hát của bài mẫu) ---------------------------------------------


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("đ", "d"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_GENERIC = {"official", "audio", "video", "music", "lyrics", "lyric", "topic", "remix", "cover", "live", "mv",
            "official audio", "official video", "official music video", "vevo", "records", "channel"}


def names_to_avoid(r: dict) -> list[str]:
    """Tên nghệ sĩ/kênh/bài của bài mẫu (từ thông tin trang) — không được xuất hiện trong prompt, lời, nội dung đăng."""
    out: list[str] = []
    for ref in (jget(r, "research", {}) or {}).get("references", []):
        meta = ref.get("meta") or {}
        for k in ("artist", "uploader", "channel", "track", "album", "title"):
            v = meta.get(k)
            if not v or not isinstance(v, str):
                continue
            parts = [v] + re.split(r"\s*[-–|:(\[\]),/]\s*|\s+ft\.?\s+|\s+feat\.?\s+|\s+x\s+", v)
            for p in parts:
                # bỏ các từ chung chung ("Official", "VEVO", "- Topic"...) để còn đúng tên
                core = " ".join(w for w in p.split() if norm(w) not in _GENERIC)
                for cand in (p.strip(), core.strip()):
                    n = norm(cand)
                    if len(n) >= 4 and n not in _GENERIC and cand not in out:
                        out.append(cand)
    return out[:30]


def find_names(text: str, names: list[str]) -> list[str]:
    t = f" {norm(text)} "
    return [n for n in names if f" {norm(n)} " in t]


# --- tệp của yêu cầu -------------------------------------------------------------------------


def safe_name(name: str, default: str) -> str:
    stem = "".join(ch for ch in Path(name or "").stem if ch.isalnum() or ch in "-_")[:40]
    return stem or default


def tracks(rid: int) -> list[dict]:
    return db.qa("SELECT * FROM tracks WHERE request_id=? ORDER BY idx", rid)


def files(rid: int, kind: str) -> list[dict]:
    return db.qa("SELECT * FROM request_files WHERE request_id=? AND kind=? ORDER BY id", rid, kind)


def reset_tracks(r: dict, titles: list[str]) -> None:
    """Bộ prompt mới → danh sách bài mới; file nhạc cũ (của prompt cũ) bị gỡ."""
    for tr in tracks(r["id"]):
        if tr["audio_path"]:
            Path(tr["audio_path"]).unlink(missing_ok=True)
    db.execute("DELETE FROM tracks WHERE request_id=?", r["id"])
    for i, t in enumerate(titles):
        db.execute("INSERT INTO tracks(request_id, idx, title) VALUES(?,?,?)", r["id"], i, t)


def missing_for_production(r: dict) -> list[str]:
    miss = [f"bài {t['idx'] + 1} ({t['title']})" for t in tracks(r["id"]) if not t["audio_path"]]
    if not files(r["id"], "image"):
        miss.append("ít nhất 1 ảnh nền")
    return miss


def cancel_open_jobs(rid: int) -> int:
    n = 0
    for j in db.qa("SELECT id FROM jobs WHERE request_id=? AND status IN ('queued','awaiting_approval','awaiting_connection',"
                   "'awaiting_quota','needs_reconcile','running')", rid):
        queue.cancel(j["id"])
        n += 1
    return n


def remove_dir(r: dict, sub: str) -> None:
    shutil.rmtree(rdir(r) / sub, ignore_errors=True)
