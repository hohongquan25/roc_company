"""Các bước công việc của một yêu cầu nhạc. Mỗi handler nhận Ctx, lưu dấu mốc, ghi BÁO CÁO lên dòng thời gian
của yêu cầu, rồi chuyển bước kế tiếp bằng cách thêm việc mới (idempotency key theo yêu cầu + phiên bản)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import db
from ..config import get_settings
from ..events import emit
from ..media import analysis, ff, render, verify
from . import ai, memory, prompts, queue, reqs, roles, teams
from .errors import Cancelled, Fatal, Retryable

JOB_VI = {
    "lead_plan": "Lập kế hoạch & phân công", "research": "Nghiên cứu bài mẫu", "write": "Lập lại dàn bài", "research_track": "Định hướng một bài", "sheet_push": "Đẩy lên Google Sheet", "write_track": "Viết prompt một bài", "check_track": "Kiểm tra prompt một bài",
    "check": "Kiểm tra prompt", "produce": "Dựng video nhạc", "verify": "Kiểm tra video",
    "summary": "Báo cáo tổng kết", "report": "Báo cáo văn phòng",
}
ROLE_OF = {"lead_plan": "lead", "research": "research", "write": "writer", "research_track": "research", "write_track": "writer", "check": "qa", "check_track": "qa", "produce": "production",
           "verify": "qa", "summary": roles.EXEC_ROLE, "report": roles.EXEC_ROLE}
# bước do chương trình chạy trên "máy dùng chung" của văn phòng (không gọi AI)
MACHINE_OF = {"produce": "render"}


@dataclass
class Ctx:
    job: dict
    worker_id: str
    heartbeat: Callable[[], None]

    def __post_init__(self) -> None:
        self.payload: dict = db.jload(self.job["payload"], {})
        self.checkpoint: dict = db.jload(self.job["checkpoint"], {})
        self.team = teams.get_team(self.job["team_id"]) if self.job["team_id"] else None
        self.req = reqs.get(self.job["request_id"]) if self.job["request_id"] else None

    def reload(self) -> dict:
        self.req = reqs.get(self.req["id"])
        return self.req

    def cancelled(self) -> bool:
        return queue.is_cancel_requested(self.job["id"])

    def save(self, **kv: Any) -> None:
        self.checkpoint.update(kv)
        queue.save_checkpoint(self.job["id"], self.checkpoint)

    def log(self, msg: str, level: str = "info", data: Any = None) -> None:
        emit("job.progress", msg, team_id=self.job["team_id"], job_id=self.job["id"],
             request_id=self.job["request_id"], level=level, data=data)

    def report(self, title: str, body: str, data: Any = None) -> None:
        reqs.add_report(self.req["id"], self.job["kind"], ROLE_OF.get(self.job["kind"]), title, body, data,
                        job_id=self.job["id"])

    def workdir(self) -> Path:
        d = teams.team_dir(self.team) / "jobs" / str(self.job["id"])
        d.mkdir(parents=True, exist_ok=True)
        return d


def ask(ctx: Ctx, role: str, task: str, sections: list[prompts.Section], schema: dict, scope: str,
        resume: bool = False) -> ai.CliResult:
    emp = ai.employee_for(role)
    mem = memory.for_job(ctx.team["id"] if ctx.team else None, ctx.job["id"])
    if ctx.job.get("attempts", 0) > 1 and ctx.job.get("error_kind") == "invalid_output":
        task += f"\nLần thực hiện trước bị từ chối vì: {(ctx.job['error'] or '')[:400]}. Hãy khắc phục điều đó."
    prompt = prompts.build(instructions=emp["instructions"], memory=mem, task=task, sections=sections,
                           schema=schema, team_name=ctx.team["name"] if ctx.team else None)
    return ai.run_employee(job=ctx.job, employee=emp, prompt=prompt, schema=schema, workdir=ctx.workdir(),
                           scope_key=scope, cancel_check=ctx.cancelled, resume=resume)


@contextmanager
def busy(role: str, job_id: int) -> Iterator[None]:
    """Bước chạy bằng chương trình nhưng do một nhân viên phụ trách → văn phòng hiện người đó đang làm."""
    emp = db.q1("SELECT id FROM employees WHERE role=?", role)
    if emp:
        db.execute("UPDATE employees SET status='working', current_job_id=? WHERE id=?", job_id, emp["id"])
    try:
        yield
    finally:
        if emp:
            queue.release_employee(emp["id"], job_id)


def _req_section(r: dict) -> prompts.Section:
    return prompts.Section("Yêu cầu của giám đốc", reqs.request_brief_md(r))


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- (không có)"


# --- 1. trưởng nhóm lập kế hoạch ------------------------------------------------------------


def h_lead_plan(ctx: Ctx) -> dict:
    r = ctx.req
    staff = {e["role"]: e["name"] for e in db.qa("SELECT role, name FROM employees")}
    res = ask(ctx, "lead", "Lập kế hoạch thực hiện yêu cầu nhạc dưới đây và phân công cho từng nhân viên.", [
        _req_section(r),
        prompts.Section("Nhân viên trong văn phòng", "\n".join(
            f"- {staff.get(k, '?')}: {v}" for k, v in roles.ROLES.items() if k != "lead")),
        prompts.Section("Quy trình cố định", "Lan đo bài mẫu song song với bạn. Sau đó từng bài chạy dây chuyền: "
                        "Lan định hướng bài → Hùng viết prompt Suno → Mai soát → đẩy lên Google Sheet. "
                        "Dàn bài bạn lập là đầu vào của dây chuyền."),
    ], roles.S_LEAD, scope=f"lead:r{r['id']}")
    d = res.data
    body = "\n".join([d["summary"], "", "**Phân công:**",
                      *[f"- **{a['who']}**: {a['task']}" for a in d["assignments"]],
                      "", "**Rủi ro cần lưu ý:**", _bullets(d.get("risks") or [])])
    n = r["track_count"]
    if len(d["tracks"]) < n:
        raise Retryable("invalid_output", f"Dàn bài cần đúng {n} bài, nhận được {len(d['tracks'])}")
    _check_youtube_names(r, d["youtube"])
    plan = {k: d[k] for k in ("summary", "assignments", "risks") if k in d}
    db.execute("UPDATE requests SET lead_plan=?, updated_at=? WHERE id=?", db.jdump(plan), db.now(), r["id"])
    ver = _save_outline(r, d, [])
    body += "\n\n**Dàn bài:**\n" + "\n".join(f"{i + 1}. **{t['title']}** — {t['concept']}"
                                             for i, t in enumerate(d["tracks"][:n]))
    ctx.report("Kế hoạch & dàn bài", body, plan)
    _kick(r["id"])
    return {"assignments": len(d["assignments"]), "version": ver}


# --- 2. nghiên cứu bài mẫu -------------------------------------------------------------------


def _measure(ctx: Ctx) -> tuple[list[dict], list[dict]]:
    """Đo bài mẫu: file bạn tải lên (nếu có) và/hoặc tải từ link. Âm thanh bị xóa ngay sau khi đo."""
    r, s = ctx.req, get_settings()
    refs: list[dict] = list(ctx.checkpoint.get("refs") or [])
    failures: list[dict] = list(ctx.checkpoint.get("failures") or [])
    done = {x.get("source") for x in refs} | {x.get("link") for x in failures}
    work = ctx.workdir() / "ref"
    for f in reqs.files(r["id"], "reference_audio"):
        src = Path(f["path"])
        key = f"file:{f['id']}"
        if key not in done:
            try:
                m = analysis.measure_and_forget(src, work / str(f["id"]))
                refs.append({"source": key, "name": f["filename"], "meta": {"title": None}, **m})
            except (ff.FFmpegError, ValueError) as e:
                src.unlink(missing_ok=True)
                failures.append({"link": key, "name": f["filename"], "error": f"không đọc được file: {e}"[:300]})
        db.execute("DELETE FROM request_files WHERE id=?", f["id"])
        ctx.save(refs=refs, failures=failures)
        ctx.heartbeat()
    if not ctx.payload.get("skip_links"):
        for url in reqs.jget(r, "links", []):
            if url in done:
                continue
            if ctx.cancelled():
                raise Cancelled("đã hủy giữa bước nghiên cứu")
            ctx.log(f"Lan tải âm thanh bài mẫu để đo: {url}")
            try:
                path, meta = analysis.download_reference(url, work / "dl", s.reference_max_seconds)
                m = analysis.measure_and_forget(path, work / "a")
                refs.append({"source": url, "name": meta.get("title") or url, "meta": meta, **m})
            except analysis.DownloadError as e:
                failures.append({"link": url, "error": str(e)})
            except (ff.FFmpegError, ValueError) as e:
                failures.append({"link": url, "error": f"không phân tích được: {e}"[:300]})
            finally:
                for p in (work / "dl").glob("*") if (work / "dl").exists() else []:
                    p.unlink(missing_ok=True)  # không giữ lại âm thanh bài của người khác
            ctx.save(refs=refs, failures=failures)
            ctx.heartbeat()
    return refs, failures


def _ref_brief(m: dict) -> dict:
    """Số đo gọn để đưa cho AI (bỏ đường năng lượng chi tiết)."""
    return {k: m[k] for k in ("name", "duration_sec", "tempo_bpm", "onsets_per_sec", "key", "loudness_lufs",
                              "true_peak_dbtp", "loudness_range_lu", "dynamic_range_db", "brightness_hz", "sections")
            if k in m}


def h_research(ctx: Ctx) -> dict:
    r = ctx.req
    refs, failures = _measure(ctx)
    if not refs and failures:
        body = "\n".join(["Lan không lấy được âm thanh của bài mẫu nên chưa thể đo:", "",
                          *[f"- {f.get('name') or f['link']}: {f['error']}" for f in failures], "",
                          "**Cần bạn:** tải file âm thanh của bài mẫu (mp3/wav/m4a) lên ở trang yêu cầu. "
                          "File chỉ dùng để đo và bị xóa ngay sau khi phân tích."])
        ctx.report("Không tải được bài mẫu", body, {"failures": failures})
        reqs.set_stage(r["id"], "awaiting_reference", "Tải file bài mẫu lên để tiếp tục")
        return {"awaiting_reference": True}
    sections = [_req_section(r)]
    if refs:
        sections.append(prompts.Section("Số đo thật của bài mẫu (chương trình đo bằng librosa + FFmpeg)",
                                        json.dumps([_ref_brief(m) for m in refs], ensure_ascii=False)))
        sections.append(prompts.Section("Thông tin trang của bài mẫu", json.dumps(
            [m.get("meta") or {} for m in refs], ensure_ascii=False), trusted=False))
    else:
        sections.append(prompts.Section("Số đo", "Không có bài mẫu — chỉ dựa trên thể loại và ghi chú của giám đốc."))
    res = ask(ctx, "research", "Diễn giải phong cách âm nhạc để biên tập viên viết prompt Suno: nhịp độ, tông, nhạc cụ, "
                               "cảm xúc, cấu trúc, chất âm. Ghi rõ đâu là số đo, đâu là suy luận.",
              sections, roles.S_RESEARCH, scope=f"research:r{r['id']}")
    d = res.data
    data = {**d, "references": refs, "failures": failures}
    db.execute("UPDATE requests SET research=?, updated_at=? WHERE id=?", db.jdump(data), db.now(), r["id"])
    lines = [d["summary"], ""]
    if refs:
        lines += ["**Số đo thật (chương trình):**", *[f"- {m['name']}: {analysis.describe(m)}" for m in refs], ""]
    if failures:
        lines += ["**Không đo được:**", *[f"- {f.get('name') or f['link']}: {f['error']}" for f in failures], ""]
    lines += [f"**Nhịp độ:** {d['tempo']}", f"**Tông/điệu thức:** {d['key_mode']}",
              f"**Nhạc cụ:** {', '.join(d['instruments']) or '—'}", f"**Cảm xúc:** {', '.join(d['mood']) or '—'}",
              "", "**Cấu trúc:**", _bullets(d["structure"]), "", "**Chất âm / sản xuất:**",
              _bullets(d.get("production_notes") or []), "", "**Gợi ý cho prompt:**", _bullets(d["recommendations"]),
              "", "_Âm thanh bài mẫu đã bị xóa sau khi đo; ROC chỉ giữ con số._" if refs else ""]
    ctx.report("Báo cáo nghiên cứu phong cách", "\n".join(lines).strip(), {"references": [_ref_brief(m) for m in refs]})
    _kick(r["id"])
    return {"references": len(refs), "failures": len(failures)}


# --- 3. viết prompt Suno ---------------------------------------------------------------------


def _research_section(r: dict) -> prompts.Section:
    rs = reqs.jget(r, "research", {}) or {}
    brief = {k: rs.get(k) for k in ("summary", "tempo", "key_mode", "instruments", "mood", "structure",
                                    "production_notes", "recommendations")}
    brief["measurements"] = [_ref_brief(m) for m in rs.get("references", [])]
    return prompts.Section("Báo cáo nghiên cứu", json.dumps(brief, ensure_ascii=False))


def _lyrics_rule(r: dict) -> str:
    if r["vocals"] == "vocal":
        return f"Kèm lời MỚI bằng ngôn ngữ {r['lyrics_language']} có nhãn [Verse]/[Chorus]/[Bridge]."
    return "Nhạc KHÔNG LỜI: để lyrics là chuỗi rỗng."


def _update_prompts(rid: int, ver: int, fn: Callable[[dict], Any]) -> tuple[dict | None, Any]:
    """Đọc-sửa-ghi bộ prompt trong một giao dịch (Hùng và Mai cùng sửa bộ prompt song song).
    Bỏ qua (trả None) nếu bộ prompt đã sang phiên bản khác."""
    with db.tx() as c:
        row = c.execute("SELECT prompts FROM requests WHERE id=?", (rid,)).fetchone()
        data = db.jload(row[0] if row else None, None)
        if not data or data.get("version") != ver:
            return None, None
        out = fn(data)
        c.execute("UPDATE requests SET prompts=?, updated_at=? WHERE id=?", (db.jdump(data), db.now(), rid))
        return data, out


def _check_youtube_names(r: dict, yt: dict) -> None:
    found = reqs.find_names(" ".join([yt.get("title", ""), yt.get("description", ""), " ".join(yt.get("tags") or [])]),
                            reqs.names_to_avoid(r))
    if found:
        raise Retryable("invalid_output", f"Nội dung đăng YouTube có tên của bài mẫu: {', '.join(found)} — phải bỏ")


def _save_outline(r: dict, d: dict, fb: list[str]) -> int:
    n = r["track_count"]
    tracks = [{"title": " ".join(t["title"].split()), "concept": t["concept"].strip(), "style": "", "lyrics": "",
               "state": "planned"} for t in d["tracks"][:n]]
    ver = db.q1("SELECT prompts_version FROM requests WHERE id=?", r["id"])["prompts_version"] + 1
    data = {"version": ver, "tracks": tracks, "image_prompts": d["image_prompts"], "youtube": d["youtube"],
            "tracklist_template": d.get("tracklist_template") or "", "feedback": fb}
    db.execute("UPDATE requests SET prompts=?, prompts_version=?, revisions=0, updated_at=? WHERE id=?",
               db.jdump(data), ver, db.now(), r["id"])
    reqs.reset_tracks(r, [t["title"] for t in tracks])
    return ver


def _kick(rid: int) -> None:
    """Có cả dàn bài (Minh) lẫn nghiên cứu (Lan) → bắt đầu dây chuyền từ bài 1. Gọi từ cả hai phía;
    khóa idempotency bảo đảm chỉ một việc được tạo."""
    r = reqs.get(rid)
    data = reqs.jget(r, "prompts")
    if not data or not r["research"] or r["stage"] in ("cancelled", "done", "awaiting_reference"):
        return
    ver = data["version"]
    reqs.enqueue(r, "research_track", {"version": ver, "idx": 0, "feedback": data.get("feedback") or []},
                 key=f"v{ver}t0")
    if r["stage"] != "writing":
        reqs.set_stage(rid, "writing", "Dây chuyền từng bài: Lan → Hùng → Mai")


def h_research_track(ctx: Ctx) -> dict:
    """Lan định hướng riêng cho một bài (dựa trên số đo + ý tưởng của Minh) rồi đẩy ngay cho Hùng,
    và chuyển sang bài kế tiếp."""
    r = ctx.req
    p = ctx.payload
    ver, idx = p["version"], p["idx"]
    data = reqs.jget(r, "prompts")
    if not data or data.get("version") != ver:
        return {"stale": True}
    tracks = data["tracks"]
    me = tracks[idx]
    res = ask(ctx, "research", f"Viết định hướng âm nhạc riêng cho bài {idx + 1}/{len(tracks)} \"{me['title']}\" "
                               f"(ý tưởng của trưởng nhóm: {me.get('concept', '')}) để biên tập viên viết prompt Suno. "
                               "Dựa trên báo cáo nghiên cứu; nêu nhịp độ, tông, nhạc cụ chính, năng lượng, điểm khác "
                               "các bài còn lại.", [
        _req_section(r), _research_section(r),
        prompts.Section("Các bài khác trong bộ", "\n".join(
            f"{i + 1}. {t['title']} — {t.get('concept', '')}" for i, t in enumerate(tracks) if i != idx) or "(không có)"),
    ], roles.S_TRACK_BRIEF, scope=f"research:r{r['id']}:t{idx}")
    brief = res.data

    def put(x: dict) -> None:
        x["tracks"][idx]["brief"] = brief
        x["tracks"][idx]["state"] = "writing"
    if _update_prompts(r["id"], ver, put)[0] is None:
        return {"stale": True}
    ctx.report(f"Định hướng bài {idx + 1} (phiên bản {ver})", "\n".join([
        f"**{idx + 1}. {me['title']}**", "", brief["direction"], "",
        f"**Nhịp độ:** {brief.get('tempo') or '—'} · **Tông:** {brief.get('key') or '—'}",
        f"**Nhạc cụ chính:** {', '.join(brief.get('instruments') or []) or '—'}"]))
    fb = p.get("feedback") or []
    reqs.enqueue(r, "write_track", {"version": ver, "idx": idx, "feedback": fb, "rev": 0}, key=f"v{ver}t{idx}r0")
    if idx + 1 < len(tracks):
        reqs.enqueue(r, "research_track", {"version": ver, "idx": idx + 1, "feedback": fb}, key=f"v{ver}t{idx + 1}")
    return {"version": ver, "idx": idx}


def h_write(ctx: Ctx) -> dict:
    """Hùng lập DÀN BÀI (tên + ý tưởng từng bài, ảnh nền, YouTube), rồi viết từng bài ở việc write_track:
    bài nào xong thì Mai soát ngay bài đó trong khi Hùng viết bài kế tiếp."""
    r = ctx.req
    n = r["track_count"]
    fb = ctx.payload.get("feedback") or []
    prev = reqs.jget(r, "prompts")
    names = reqs.names_to_avoid(r)
    long_ = r["output_type"] in ("long", "both")
    task = (f"Lập dàn bài cho đúng {n} bài nhạc Suno theo yêu cầu dưới đây: mỗi bài một tên và một ý tưởng ngắn "
            "(giai điệu, nhạc cụ chính, năng lượng) — các bài cùng một chất nhưng khác nhau để nghe không trùng lặp. "
            "CHƯA viết prompt chi tiết. Thêm 2–4 gợi ý ảnh nền (mô tả hình, không chữ trong ảnh), tiêu đề/mô tả/thẻ YouTube"
            + (" và mẫu danh sách bài cho video dài (mỗi dòng: [mốc] Tên bài)." if long_ else "."))
    sections = [_req_section(r)]
    lead = reqs.jget(r, "lead_plan")
    if lead:
        sections.append(prompts.Section("Kế hoạch của trưởng nhóm", lead.get("summary", "")))
    sections.append(_research_section(r))
    if names:
        sections.append(prompts.Section("Tên KHÔNG được dùng ở bất kỳ đâu (nghệ sĩ/kênh/bài của bài mẫu)",
                                        "\n".join(names), trusted=False))
    if fb:
        task += "\nĐây là lần sửa. Làm theo các yêu cầu sửa bên dưới; giữ nguyên phần đã đạt."
        sections.append(prompts.Section("Yêu cầu sửa (bắt buộc làm theo)", _bullets(fb)))
        if prev:
            sections.append(prompts.Section("Bộ prompt phiên bản trước", json.dumps(
                {k: prev.get(k) for k in ("tracks", "image_prompts", "youtube", "tracklist_template")}, ensure_ascii=False)))
    res = ask(ctx, "writer", task, sections, roles.S_OUTLINE, scope=f"write:r{r['id']}", resume=bool(fb))
    d = res.data
    if len(d["tracks"]) < n:
        raise Retryable("invalid_output", f"Cần đúng {n} bài, nhận được {len(d['tracks'])}")
    _check_youtube_names(r, d["youtube"])
    ver = _save_outline(r, d, fb)
    lines = [f"Dàn bài phiên bản {ver} — {n} bài{' (sửa theo góp ý)' if fb else ''}. Dây chuyền Lan → Hùng → Mai "
             "chạy lại từng bài.", ""]
    lines += [f"{i + 1}. **{t['title']}** — {t['concept']}" for i, t in enumerate(d["tracks"][:n])]
    ctx.report(f"Dàn bài phiên bản {ver}", "\n".join(lines))
    _kick(r["id"])
    return {"version": ver, "tracks": n}


def h_write_track(ctx: Ctx) -> dict:
    r = ctx.req
    p = ctx.payload
    ver, idx, rev = p["version"], p["idx"], p.get("rev", 0)
    fb = p.get("feedback") or []
    data = reqs.jget(r, "prompts")
    if not data or data.get("version") != ver:
        return {"stale": True}
    tracks = data["tracks"]
    me = tracks[idx]
    others = [f"{i + 1}. {t['title']} — {t.get('concept', '')}" + (f"\n   style: {t['style']}" if t.get("style") else "")
              for i, t in enumerate(tracks) if i != idx]
    names = reqs.names_to_avoid(r)
    task = (f"Viết prompt Suno cho bài {idx + 1}/{len(tracks)} \"{me['title']}\" (ý tưởng: {me.get('concept', '')}). "
            f"Prompt phong cách ≤ {roles.STYLE_MAX} ký tự, tiếng Anh. {_lyrics_rule(r)} "
            "Bài này phải khác rõ các bài còn lại trong bộ (xem danh sách) nhưng vẫn cùng một chất. "
            "Giữ tên bài trừ khi được yêu cầu đổi.")
    sections = [_req_section(r), _research_section(r),
                prompts.Section("Định hướng của Lan cho bài này", json.dumps(me.get("brief") or {}, ensure_ascii=False)),
                prompts.Section("Các bài khác trong bộ", "\n".join(others) or "(không có)")]
    if names:
        sections.append(prompts.Section("Tên KHÔNG được dùng ở bất kỳ đâu (nghệ sĩ/kênh/bài của bài mẫu)",
                                        "\n".join(names), trusted=False))
    if fb:
        task += "\nLàm theo các yêu cầu sửa bên dưới."
        sections.append(prompts.Section("Yêu cầu sửa (bắt buộc làm theo)", _bullets(fb)))
        if me.get("style"):
            sections.append(prompts.Section("Bản trước của bài này", json.dumps(
                {k: me.get(k) for k in ("title", "style", "lyrics")}, ensure_ascii=False)))
    res = ask(ctx, "writer", task, sections, roles.S_TRACK, scope=f"write:r{r['id']}:t{idx}", resume=rev > 0)
    d = res.data
    t = {"title": " ".join(d["title"].split()), "style": d["style"].strip(),
         "lyrics": "" if r["vocals"] == "instrumental" else (d.get("lyrics") or "").strip(),
         "concept": me.get("concept", ""), "brief": me.get("brief"), "state": "checking", "rev": rev}
    if d.get("notes"):
        t["notes"] = d["notes"]

    def put(x: dict) -> None:
        x["tracks"][idx] = t
    if _update_prompts(r["id"], ver, put)[0] is None:
        return {"stale": True}
    db.execute("UPDATE tracks SET title=? WHERE request_id=? AND idx=?", t["title"], r["id"], idx)
    lines = [f"**{idx + 1}. {t['title']}** ({len(t['style'])}/{roles.STYLE_MAX} ký tự)"
             + (f" — sửa lần {rev}" if rev else ""), "", "```", t["style"], "```"]
    if t["lyrics"]:
        lines += ["", "_Lời (trích):_", "", "```", "\n".join(t["lyrics"].splitlines()[:6]), "```"]
    ctx.report(f"Prompt bài {idx + 1} (phiên bản {ver}){' — sửa' if rev else ''}", "\n".join(lines))
    # Mai soát ngay bài này (bài sửa được ưu tiên trước bài mới)
    reqs.enqueue(r, "check_track", {"version": ver, "idx": idx, "rev": rev}, key=f"v{ver}t{idx}r{rev}", priority=90)
    return {"version": ver, "idx": idx, "rev": rev}


def h_check_track(ctx: Ctx) -> dict:
    r = ctx.req
    ver, idx, rev = ctx.payload["version"], ctx.payload["idx"], ctx.payload.get("rev", 0)
    data = reqs.jget(r, "prompts")
    if not data or data.get("version") != ver:
        return {"stale": True}
    t = data["tracks"][idx]
    det = [i for i in deterministic_issues(r, {"tracks": [t]}, base=idx) if i.get("track") is not None]
    res = ask(ctx, "qa", f"Soát prompt Suno của bài {idx + 1} \"{t['title']}\": không tên nghệ sĩ/bài hát/thương hiệu "
                         "có thật, lời không chép hay phỏng theo bài có sẵn, khớp nghiên cứu và yêu cầu, prompt rõ ràng "
                         "dùng được, không trùng lặp với các bài khác trong bộ. verdict='revise' nếu có vấn đề cần sửa.", [
        _req_section(r), _research_section(r),
        prompts.Section("Bài cần kiểm tra", json.dumps({k: t.get(k) for k in ("title", "style", "lyrics")},
                                                       ensure_ascii=False)),
        prompts.Section("Các bài khác trong bộ", "\n".join(
            f"{i + 1}. {x['title']}: {x.get('style') or x.get('concept', '')}"
            for i, x in enumerate(data["tracks"]) if i != idx) or "(không có)"),
    ], roles.S_CHECK, scope=f"check:r{r['id']}:t{idx}")
    ai_issues = ([{"track": idx, "reason": i["reason"]} for i in res.data["issues"]]
                 if res.data["verdict"] == "revise" else [])
    issues = det + ai_issues
    max_rev = get_settings().max_revisions
    state = "ok" if not issues else ("revising" if rev < max_rev else "escalated")

    def mark(x: dict) -> bool:
        x["tracks"][idx]["state"] = state
        if issues:
            x["tracks"][idx]["issues"] = [i["reason"] for i in issues]
        else:
            x["tracks"][idx].pop("issues", None)
        return all(tr.get("state") in ("ok", "escalated") for tr in x["tracks"])
    saved, all_done = _update_prompts(r["id"], ver, mark)
    if saved is None:
        return {"stale": True}
    lines = [res.data["summary"] or "", ""]
    if det:
        lines += ["**Kiểm tra bằng chương trình:**", *[f"- {i['reason']}" for i in det], ""]
    if issues:
        lines += ["**Vấn đề cần sửa:**", *[f"- {i['reason']}" for i in issues]]
    else:
        lines.append("✅ Đạt.")
    verdict = {"ok": "ĐẠT", "revising": "CẦN SỬA", "escalated": "CẦN BẠN XEM"}[state]
    ctx.report(f"Kiểm tra bài {idx + 1} (phiên bản {ver}): {verdict}", "\n".join(lines).strip(),
               {"verdict": "pass" if state == "ok" else "revise", "issues": issues, "track": idx})
    if state == "ok":
        sheet_push(r, ver, idx, saved["tracks"][idx])
    if state == "revising":
        reqs.enqueue(r, "write_track", {"version": ver, "idx": idx, "rev": rev + 1,
                                        "feedback": [i["reason"] for i in issues]},
                     key=f"v{ver}t{idx}r{rev + 1}", priority=80)
    if all_done and reqs.get(r["id"])["stage"] == "writing":
        esc = [i + 1 for i, tr in enumerate(saved["tracks"]) if tr.get("state") == "escalated"]
        note = (f"Mai vẫn thấy vấn đề ở bài {', '.join(map(str, esc))} sau {max_rev} vòng sửa — bạn quyết định"
                if esc else None)
        if esc:
            reqs.set_stage(r["id"], "awaiting_prompt_approval", note)
        else:
            reqs.add_report(r["id"], "prompts_done", None, f"Hoàn thành bộ prompt phiên bản {ver}",
                            f"Mai đã soát đạt cả {len(saved['tracks'])} bài; prompt đã đẩy lên Google Sheet "
                            "(nếu đã cấu hình). Muốn làm video thì bấm **Dựng video** ở trang yêu cầu.")
            reqs.set_stage(r["id"], "done")
    return {"verdict": state, "idx": idx}


# --- Google Sheet ----------------------------------------------------------------------------


def sheet_push(r: dict, ver: int, idx: int, t: dict) -> None:
    """Xếp việc đẩy một bài lên Google Sheet. Việc riêng, không gắn yêu cầu: lỗi mạng không làm hỏng yêu cầu."""
    if not get_settings().sheets_webhook_url:
        return
    row = {"time": db.now(), "request_id": r["id"], "request": r["title"], "genre": r["genre"], "version": ver,
           "track_no": idx + 1, "title": t["title"], "style": t["style"], "lyrics": t.get("lyrics") or "",
           "vocals": r["vocals"], "state": t.get("state")}
    queue.enqueue("sheet_push", team_id=r["team_id"], payload=row, idempotency_key=f"sheet:r{r['id']}:v{ver}t{idx}",
                  max_attempts=5)


def h_sheet_push(ctx: Ctx) -> dict:
    import urllib.error
    import urllib.request
    url = get_settings().sheets_webhook_url
    if not url:
        return {"skipped": True}
    req = urllib.request.Request(url, data=json.dumps(ctx.payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(2000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Retryable("network", f"Không gửi được lên Google Sheet: {e}")
    if '"ok":true' not in body.replace(" ", ""):
        raise Retryable("network", f"Google Sheet trả lời lạ: {body[:300]}")
    return {"ok": True}


# --- 4. kiểm tra prompt ----------------------------------------------------------------------


def deterministic_issues(r: dict, data: dict, base: int = 0) -> list[dict]:
    """Lỗi chắc chắn, kiểm bằng chương trình (không cần AI): độ dài prompt, tên bài mẫu bị lọt, thiếu lời."""
    issues: list[dict] = []
    names = reqs.names_to_avoid(r)
    yt = data.get("youtube") or {}
    for i, t in enumerate(data.get("tracks") or [], start=base):
        if len(t.get("style", "")) > roles.STYLE_MAX:
            issues.append({"track": i, "reason": f"Prompt bài {i + 1} dài {len(t['style'])} ký tự (> {roles.STYLE_MAX})"})
        found = reqs.find_names(" ".join([t.get("title", ""), t.get("style", ""), t.get("lyrics", "")]), names)
        if found:
            issues.append({"track": i, "reason": f"Bài {i + 1} có tên của bài mẫu: {', '.join(found)} — phải bỏ"})
        if r["vocals"] == "vocal" and len((t.get("lyrics") or "").strip()) < 20:
            issues.append({"track": i, "reason": f"Bài {i + 1} được yêu cầu có lời nhưng chưa có lời"})
    found = reqs.find_names(" ".join([yt.get("title", ""), yt.get("description", ""), " ".join(yt.get("tags") or [])]), names)
    if found:
        issues.append({"track": None, "reason": f"Nội dung đăng YouTube có tên của bài mẫu: {', '.join(found)}"})
    return issues


def h_check(ctx: Ctx) -> dict:
    r = ctx.req
    data = reqs.jget(r, "prompts")
    if not data:
        raise Fatal("missing_input", "chưa có bộ prompt")
    det = deterministic_issues(r, data)
    res = ask(ctx, "qa", "Soát bộ prompt Suno và lời: không tên nghệ sĩ/bài hát/thương hiệu có thật, lời không chép hay "
                         "phỏng theo bài có sẵn, khớp nghiên cứu và yêu cầu, prompt rõ ràng dùng được. "
                         "verdict='revise' nếu có vấn đề cần sửa; ghi rõ bài số mấy (track tính từ 0).", [
        _req_section(r), _research_section(r),
        prompts.Section("Bộ prompt cần kiểm tra", json.dumps(
            {k: data.get(k) for k in ("tracks", "youtube", "tracklist_template")}, ensure_ascii=False)),
    ], roles.S_CHECK, scope=f"check:r{r['id']}")
    ai_issues = res.data["issues"] if res.data["verdict"] == "revise" else []
    issues = det + ai_issues
    lines = [res.data["summary"] or "", ""]
    if det:
        lines += ["**Kiểm tra bằng chương trình:**", *[f"- {i['reason']}" for i in det], ""]
    if issues:
        lines += ["**Vấn đề cần sửa:**", *[f"- {('Bài ' + str(i['track'] + 1) + ': ') if isinstance(i.get('track'), int) else ''}"
                                           f"{i['reason']}" for i in issues]]
    else:
        lines.append("✅ Đạt: không tên nghệ sĩ/bài hát, độ dài prompt hợp lệ, khớp nghiên cứu.")
    verdict = "pass" if not issues else "revise"
    ctx.report(f"Kiểm tra prompt phiên bản {data['version']}: {'ĐẠT' if verdict == 'pass' else 'CẦN SỬA'}",
               "\n".join(lines).strip(), {"verdict": verdict, "issues": issues})
    if verdict == "pass":
        reqs.set_stage(r["id"], "awaiting_prompt_approval")
        return {"verdict": "pass"}
    revs = r["revisions"] + 1
    db.execute("UPDATE requests SET revisions=? WHERE id=?", revs, r["id"])
    if revs > get_settings().max_revisions:
        reqs.set_stage(r["id"], "awaiting_prompt_approval",
                       f"Mai vẫn thấy vấn đề sau {revs - 1} vòng sửa — bạn quyết định duyệt hay yêu cầu sửa")
        return {"verdict": "escalated"}
    reqs.set_stage(r["id"], "writing", f"Mai yêu cầu sửa (vòng {revs})")
    reqs.enqueue(r, "write", {"feedback": [i["reason"] for i in issues]}, key=f"v{data['version']}rev{revs}")
    return {"verdict": "revise", "round": revs}


# --- 6. dựng video ---------------------------------------------------------------------------


def h_produce(ctx: Ctx) -> dict:
    r, s = ctx.req, get_settings()
    trs = reqs.tracks(r["id"])
    images = [Path(f["path"]) for f in reqs.files(r["id"], "image")]
    miss = reqs.missing_for_production(r)
    if miss:
        raise Fatal("missing_input", "Thiếu: " + ", ".join(miss))
    pdata = reqs.jget(r, "prompts", {}) or {}
    yt = pdata.get("youtube") or {}
    out = reqs.rdir(r) / "output" / f"j{ctx.job['id']}"
    (out / "audio").mkdir(parents=True, exist_ok=True)
    size = (s.video_width, s.video_height)
    norm = dict(ctx.checkpoint.get("norm") or {})
    try:
        # chuẩn hóa độ lớn từng bài (dấu mốc: bài đã xong thì không làm lại)
        for t in trs:
            key = str(t["idx"])
            dst = out / "audio" / f"track_{t['idx']:02d}.wav"
            if key in norm and dst.exists():
                continue
            if ctx.cancelled():
                raise Cancelled("đã hủy giữa bước dựng")
            norm[key] = render.normalize(Path(t["audio_path"]), dst, s.target_lufs)
            ctx.save(norm=norm)
            ctx.log(f"Chuẩn hóa âm thanh bài {t['idx'] + 1}/{len(trs)}: "
                    f"{norm[key]['before']['i']:.1f} → {norm[key]['after']['i']:.1f} LUFS")
            ctx.heartbeat()
        outputs: dict = {"dir": str(out), "job_id": ctx.job["id"], "singles": [], "long": None,
                         "created_at": db.now()}
        heading = yt.get("title") or r["title"]
        if r["output_type"] in ("single", "both"):
            for t in trs:
                if ctx.cancelled():
                    raise Cancelled("đã hủy giữa bước dựng")
                img = images[t["idx"] % len(images)]
                vid = out / f"single_{t['idx']:02d}.mp4"
                res = render.render_track_video(image=img, audio=out / "audio" / f"track_{t['idx']:02d}.wav",
                                                title=t["title"], sub=heading, out=vid, size=size)
                thumb = render.thumbnail({}, t["title"], out / f"thumb_{t['idx']:02d}.png", image=img)
                outputs["singles"].append({"idx": t["idx"], "title": t["title"], "path": str(vid), "thumb": str(thumb),
                                           "duration": res["duration"], "image": img.name})
                ctx.log(f"Dựng xong video bài {t['idx'] + 1}/{len(trs)} ({res['duration']:.0f}s)")
                ctx.heartbeat()
        if r["output_type"] in ("long", "both"):
            parts = [(out / "audio" / f"track_{t['idx']:02d}.wav", t["title"]) for t in trs]
            vid = out / "long.mp4"
            res = render.render_long_video(image=images[0], parts=parts, heading=heading, out=vid, size=size)
            thumb = render.thumbnail({}, heading, out / "thumb_long.png", image=images[0])
            desc = (yt.get("description") or "").strip() + "\n\nDanh sách bài:\n" + res["tracklist"]
            outputs["long"] = {"path": str(vid), "thumb": str(thumb), "duration": res["duration"],
                               "chapters": res["chapters"], "tracklist": res["tracklist"], "description": desc,
                               "image": images[0].name}
            ctx.log(f"Dựng xong video dài ({render.fmt_ts(res['duration'])})")
    except ff.FFmpegError as e:
        raise Retryable("render_error", str(e)[-800:]) from e
    outputs["loudness"] = {k: {"before": v["before"]["i"], "after": v["after"]["i"]} for k, v in norm.items()}
    db.execute("UPDATE requests SET outputs=?, updated_at=? WHERE id=?", db.jdump(outputs), db.now(), r["id"])
    lines = [f"Tuấn đã dựng xong bằng FFmpeg ({size[0]}x{size[1]}, âm thanh chuẩn hóa về {s.target_lufs:.0f} LUFS).", "",
             "**Âm thanh:**"]
    for t in trs:
        v = norm[str(t["idx"])]
        lines.append(f"- Bài {t['idx'] + 1} · {t['title']}: {v['before']['i']:.1f} → {v['after']['i']:.1f} LUFS, "
                     f"{render.fmt_ts(v['duration'])}")
    if outputs["singles"]:
        lines += ["", f"**Video từng bài:** {len(outputs['singles'])} file"]
        lines += [f"- {o['title']} — {render.fmt_ts(o['duration'])} (ảnh: {o['image']})" for o in outputs["singles"]]
    if outputs["long"]:
        lines += ["", f"**Video dài:** {render.fmt_ts(outputs['long']['duration'])}", "", "```",
                  outputs["long"]["tracklist"], "```"]
    ctx.report("Dựng video xong", "\n".join(lines), {"outputs": outputs})
    reqs.set_stage(r["id"], "verifying")
    reqs.enqueue(r, "verify", key=f"j{ctx.job['id']}")
    return {"singles": len(outputs["singles"]), "long": bool(outputs["long"])}


# --- 7. kiểm tra video -----------------------------------------------------------------------


def h_verify(ctx: Ctx) -> dict:
    r, s = ctx.req, get_settings()
    outs = reqs.jget(r, "outputs")
    if not outs:
        raise Fatal("missing_input", "chưa có video để kiểm tra")
    items = [(f"Bài {o['idx'] + 1} · {o['title']}", o) for o in outs["singles"]]
    if outs.get("long"):
        items.append(("Video dài", outs["long"]))
    results, all_ok = [], True
    with busy("qa", ctx.job["id"]):
        for label, o in items:
            rep = verify.verify_output(Path(o["path"]), expected_duration=o["duration"], width=s.video_width,
                                       height=s.video_height, target_lufs=s.target_lufs)
            rep["checks"].append(verify.verify_thumbnail(Path(o["thumb"])))
            rep["ok"] = all(c["ok"] for c in rep["checks"] if c["severity"] == "error")
            all_ok &= rep["ok"]
            results.append({"label": label, **rep})
            ctx.heartbeat()
    names = {"file": "File", "video_stream": "Khung hình", "audio_stream": "Âm thanh", "duration": "Độ dài",
             "decode": "Giải mã toàn bộ", "silence": "Im lặng", "loudness": "Độ lớn", "thumbnail": "Thumbnail"}
    lines = []
    for x in results:
        lines += [f"**{x['label']}** — {'✅ đạt' if x['ok'] else '❌ không đạt'}"]
        for c in x["checks"]:
            mark = "✅" if c["ok"] else ("⚠️" if c["severity"] == "warn" else "❌")
            lines.append(f"- {mark} {names.get(c['name'], c['name'])}: {c['detail']}")
        lines.append("")
    outs["verify"] = {"ok": all_ok, "results": results}
    db.execute("UPDATE requests SET outputs=?, updated_at=? WHERE id=?", db.jdump(outs), db.now(), r["id"])
    ctx.report(f"Kiểm tra video: {'ĐẠT' if all_ok else 'KHÔNG ĐẠT'}", "\n".join(lines).strip(), {"ok": all_ok})
    if all_ok:
        reqs.set_stage(r["id"], "awaiting_final")
    else:
        reqs.set_stage(r["id"], "needs_attention", "Video không qua kiểm tra kỹ thuật — xem báo cáo của Mai")
    return {"ok": all_ok}


# --- 8. tổng kết -----------------------------------------------------------------------------


def h_summary(ctx: Ctx) -> dict:
    r = ctx.req
    reps = db.qa("SELECT author, title, body, created_at FROM request_reports WHERE request_id=? ORDER BY id", r["id"])
    timeline = [{"author": x["author"], "title": x["title"], "body": x["body"][:1200]} for x in reps]
    res = ask(ctx, roles.EXEC_ROLE, "Viết báo cáo tổng kết ngắn cho giám đốc về yêu cầu nhạc đã hoàn thành: kết quả, "
                                    "file đầu ra, điểm cần lưu ý khi đăng YouTube (đánh dấu nội dung AI, gói Suno trả phí "
                                    "để dùng thương mại), đề xuất cho lần sau. body viết bằng Markdown.", [
        _req_section(r),
        prompts.Section("Dòng thời gian báo cáo", json.dumps(timeline, ensure_ascii=False)),
    ], roles.S_SUMMARY, scope=f"summary:r{r['id']}")
    ctx.report(res.data["headline"], res.data["body"])
    reqs.set_stage(r["id"], "done")
    return {"headline": res.data["headline"]}


def h_report(ctx: Ctx) -> dict:
    since = db.now() - 86400 * int(ctx.payload.get("days", 1))
    stats = {
        "requests": {x["stage"]: x["n"] for x in db.qa("SELECT stage, COUNT(*) n FROM requests GROUP BY stage")},
        "jobs": {x["status"]: x["n"] for x in db.qa("SELECT status, COUNT(*) n FROM jobs WHERE updated_at>? GROUP BY status", since)},
        "waiting_for_you": [{"id": x["id"], "title": x["title"], "stage": reqs.STAGE_VI.get(x["stage"])}
                            for x in db.qa("SELECT id, title, stage FROM requests WHERE status='gate' ORDER BY id")],
    }
    errs = db.qa("SELECT ts, message FROM events WHERE level IN ('warn','error') AND ts>? ORDER BY id DESC LIMIT 40", since)
    res = ask(ctx, roles.EXEC_ROLE, "Viết báo cáo tiến độ văn phòng cho giám đốc từ số liệu thật dưới đây.", [
        prompts.Section("Số liệu văn phòng", json.dumps(stats, ensure_ascii=False)),
        prompts.Section("Cảnh báo/lỗi gần đây", json.dumps(errs, ensure_ascii=False), trusted=False),
    ], roles.S_REPORT, scope=f"report:{ctx.job['id']}")
    db.execute("INSERT INTO reports(team_id, kind, content, job_id, created_at) VALUES(?,?,?,?,?)", None, "daily",
               db.jdump({**res.data, "stats": stats}), ctx.job["id"], db.now())
    return {"headline": res.data["headline"]}


HANDLERS: dict[str, Callable[[Ctx], dict]] = {
    "lead_plan": h_lead_plan, "research": h_research, "write": h_write, "research_track": h_research_track, "write_track": h_write_track, "sheet_push": h_sheet_push, "check": h_check, "check_track": h_check_track, "produce": h_produce,
    "verify": h_verify, "summary": h_summary, "report": h_report,
}


def on_job_failed(job: dict, message: str) -> None:
    """Một bước của yêu cầu thất bại hẳn → yêu cầu chuyển sang 'Cần bạn xử lý' kèm báo cáo lỗi."""
    if not job.get("request_id"):
        return
    r = db.q1("SELECT * FROM requests WHERE id=?", job["request_id"])
    if not r or r["stage"] in ("done", "cancelled"):
        return
    reqs.add_report(r["id"], job["kind"], ROLE_OF.get(job["kind"]), f"{JOB_VI.get(job['kind'], job['kind'])} thất bại",
                    f"Bước này không hoàn thành sau các lần thử:\n\n```\n{message[:1500]}\n```\n\n"
                    "Bạn có thể bấm **Thử lại bước này** ở trang yêu cầu, hoặc xem chi tiết ở Bảng công việc.",
                    {"job_kind": job["kind"]}, job_id=job["id"])
    reqs.set_stage(r["id"], "needs_attention", f"{JOB_VI.get(job['kind'], job['kind'])} thất bại")
