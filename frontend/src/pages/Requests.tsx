// Yêu cầu nhạc: bạn gửi link bài mẫu + thể loại → trưởng nhóm phân công → mỗi bước có báo cáo trên dòng thời gian.
// Ba chỗ cần bạn: duyệt prompt Suno, tạo nhạc trên Suno rồi tải lên (+ ảnh nền), duyệt video thành phẩm.
import { useState } from "react";
import { api, fmtTime, Json } from "../api";
import { Act, Badge, Card, CopyBtn, Md, useData, useNav, useRoute, useToast } from "../ui";

const STAGE_COLOR: Record<string, string> = {
  done: "green", cancelled: "gray", needs_attention: "red",
  awaiting_reference: "amber", awaiting_prompt_approval: "amber", awaiting_uploads: "amber", awaiting_final: "amber",
};
export const StageBadge = ({ r }: { r: Json }) => (
  <span className={`badge ${STAGE_COLOR[r.stage] || "blue"}`}>{r.gate ? "✋ " : ""}{r.stage_vi}</span>
);

const TABS: [string, string][] = [["open", "Đang làm"], ["gate", "Chờ bạn"], ["done", "Hoàn thành"], ["cancelled", "Đã hủy"], ["all", "Tất cả"]];

export default function Requests() {
  const [, q] = useRoute();
  const [tab, setTab] = useState(q.get("tab") || "open");
  const [creating, setCreating] = useState(q.get("new") === "1");
  const { data } = useData<Json>(`/api/requests?status=${tab}`, [tab]);
  const c = data?.counts || {};
  return (
    <div>
      <h1>Yêu cầu nhạc <button className="btn primary" onClick={() => setCreating(!creating)}>{creating ? "Đóng" : "＋ Yêu cầu mới"}</button></h1>
      <p className="muted small">Gửi link bài mẫu và thể loại bạn muốn. Minh (trưởng nhóm) lên kế hoạch và phân công; Lan đo & phân tích bài mẫu;
        Hùng viết prompt Suno; Mai kiểm tra; bạn tạo nhạc trên Suno rồi tải lên; Tuấn dựng video; Mai kiểm tra video; Thảo tổng kết.
        Mỗi bước có báo cáo.</p>
      {creating && <NewRequest />}
      <div className="status-tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={`status-tab ${tab === k ? "on" : ""} ${c[k] ? "" : "zero"}`} onClick={() => setTab(k)}>{label} <b>{c[k] || 0}</b></button>
        ))}
      </div>
      <div className="appr-list">
        {!data && <div className="empty-state"><span className="muted">Đang tải…</span></div>}
        {data && !data.items.length && (
          <div className="empty-state">
            <div className="empty-icon">🎵</div>
            <b>{tab === "gate" ? "Không có yêu cầu nào chờ bạn" : "Chưa có yêu cầu nào ở mục này"}</b>
            <span className="muted">Bấm “＋ Yêu cầu mới”, dán link bài mẫu và ghi thể loại để văn phòng bắt đầu làm việc.</span>
          </div>
        )}
        {(data?.items || []).map((r: Json) => <RequestRow key={r.id} r={r} />)}
      </div>
    </div>
  );
}

export function RequestRow({ r }: { r: Json }) {
  return (
    <a className="appr req-row" href={`#/request/${r.id}`}>
      <span className="job-bar" style={{ background: r.gate ? "#f59e0b" : r.stage === "done" ? "#22c55e" : r.stage === "cancelled" ? "#94a3b8" : "#38bdf8" }} />
      <div className="appr-body">
        <header className="appr-head">
          <span className="appr-icon">🎵</span>
          <div className="appr-title">
            <b>{r.title}</b>
            <span className="muted small">#{r.id} · {r.genre} · {r.output_vi} · {r.track_count} bài · {r.vocals_vi} · cập nhật {fmtTime(r.updated_at)}</span>
          </div>
          <StageBadge r={r} />
        </header>
        {r.gate && <div className="job-reason">✋ {r.gate}</div>}
        {r.last_report && <div className="small muted">Báo cáo mới nhất: <b>{r.last_report.author}</b> — {r.last_report.title}</div>}
      </div>
    </a>
  );
}

function NewRequest() {
  const nav = useNav();
  const toast = useToast();
  const [f, setF] = useState<Json>({ links: "", genre: "", note: "", output_type: "single", track_count: 1, vocals: "instrumental", lyrics_language: "vi", title: "" });
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  return (
    <Card title="Yêu cầu mới">
      <div className="form">
        <label>Link bài mẫu (mỗi dòng một link YouTube/SoundCloud…; để trống nếu chỉ theo thể loại)
          <textarea rows={3} value={f.links} onChange={(e) => set("links", e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
        </label>
        <p className="small muted">Bài mẫu chỉ để <b>học phong cách</b> (nhịp, tông, nhạc cụ, cảm xúc). Lan tải âm thanh để đo rồi xóa ngay; không chép giai điệu hay lời.</p>
        <label>Thể loại bạn muốn <input value={f.genre} onChange={(e) => set("genre", e.target.value)} placeholder="vd: lofi chill piano để học bài, ballad buồn, EDM sôi động…" /></label>
        <label>Ghi chú thêm (tùy chọn) <textarea rows={2} value={f.note} onChange={(e) => set("note", e.target.value)} placeholder="vd: không khí mưa đêm, tránh trống quá mạnh, đối tượng người nghe…" /></label>
        <div className="row wrap gap">
          <label>Kiểu video
            <select value={f.output_type} onChange={(e) => set("output_type", e.target.value)}>
              <option value="single">Mỗi bài một video</option><option value="long">Video dài ghép nhiều bài</option><option value="both">Cả hai</option>
            </select>
          </label>
          <label>Số bài <input type="number" min={1} max={30} value={f.track_count} onChange={(e) => set("track_count", Number(e.target.value))} style={{ width: 80 }} /></label>
          <label>Lời
            <select value={f.vocals} onChange={(e) => set("vocals", e.target.value)}>
              <option value="instrumental">Không lời</option><option value="vocal">Có lời</option>
            </select>
          </label>
          {f.vocals === "vocal" && (
            <label>Ngôn ngữ lời
              <select value={f.lyrics_language} onChange={(e) => set("lyrics_language", e.target.value)}>
                <option value="vi">Tiếng Việt</option><option value="en">Tiếng Anh</option><option value="ko">Tiếng Hàn</option><option value="ja">Tiếng Nhật</option>
              </select>
            </label>
          )}
          <label className="grow">Tên yêu cầu (tùy chọn) <input value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="để trống = tự đặt theo thể loại" /></label>
        </div>
        <div className="row gap">
          <Act kind="primary" disabled={f.genre.trim().length < 2} onClick={async () => {
            const r = await api.post("/api/requests", f);
            toast("Đã gửi yêu cầu — Minh sẽ lên kế hoạch khi công ty đang hoạt động");
            nav(`/request/${r.id}`);
          }}>Gửi yêu cầu</Act>
          <span className="small muted">Văn phòng chỉ làm việc khi công ty đang ở trạng thái ▶ Bắt đầu.</span>
        </div>
      </div>
    </Card>
  );
}

// --- chi tiết một yêu cầu -------------------------------------------------------------------

export function RequestDetail({ id }: { id: number }) {
  const { data, reload } = useData<Json>(`/api/requests/${id}`, [id]);
  if (!data) return <p>Đang tải…</p>;
  const r = data.request;
  return (
    <div className="req">
      <h1>🎵 {r.title} <StageBadge r={r} /></h1>
      <div className="row wrap gap small muted">
        <span>#{r.id}</span><span>Thể loại: <b>{r.genre}</b></span><span>{r.output_vi}</span><span>{r.track_count} bài</span>
        <span>{r.vocals_vi}{r.vocals === "vocal" ? ` (${r.lyrics_language})` : ""}</span><span>gửi lúc {fmtTime(r.created_at)}</span>
      </div>
      {r.links.length > 0 && <div className="small">Bài mẫu: {r.links.map((u: string) => <a key={u} className="mr" href={u} target="_blank" rel="noreferrer noopener">{u}</a>)}</div>}
      <Gate d={data} reload={reload} />
      {data.prompts && <Prompts d={data} />}
      {(data.can_upload || data.tracks.some((t: Json) => t.has_audio)) && data.prompts && <Uploads d={data} reload={reload} />}
      {data.outputs && <Outputs d={data} />}
      <Timeline d={data} />
      {!["done", "cancelled"].includes(r.stage) && (
        <div className="row gap">
          <Act kind="danger" confirm="Hủy yêu cầu này? Các việc đang mở sẽ dừng." onClick={async () => { await api.post(`/api/requests/${id}/cancel`); reload(); }}>Hủy yêu cầu</Act>
        </div>
      )}
    </div>
  );
}

function Gate({ d, reload }: { d: Json; reload: () => void }) {
  const r = d.request;
  const toast = useToast();
  const [note, setNote] = useState("");
  const [step, setStep] = useState("produce");
  const post = async (path: string, body: Json = {}, msg = "Đã gửi") => { await api.post(`/api/requests/${r.id}/${path}`, body); toast(msg); setNote(""); reload(); };
  if (!r.gate) {
    const running = d.jobs.filter((j: Json) => ["running", "queued", "awaiting_quota", "awaiting_connection"].includes(j.status));
    if (r.stage === "done" && d.prompts && !d.outputs) {
      return (
        <Card className="gate">
          <div className="appr-actions">
            <span className="grow">✅ Bộ prompt đã xong. Làm video là việc riêng — chỉ khi bạn cần.</span>
            <Act onClick={() => post("video", {}, "Mở bước làm video — tạo nhạc trên Suno rồi tải lên")}>🎬 Dựng video</Act>
          </div>
        </Card>
      );
    }
    if (["done", "cancelled"].includes(r.stage)) return null;
    return (
      <Card className="gate busy">
        <b>⏳ {r.stage_vi}</b>{r.stage_note ? ` — ${r.stage_note}` : ""}
        {running.map((j: Json) => <div key={j.id} className="small muted">{j.kind_vi}: {j.status_vi}{j.wait_reason ? ` — ${j.wait_reason}` : ""}</div>)}
      </Card>
    );
  }
  return (
    <Card title={<>✋ Việc của bạn: {r.stage_vi}</>} className="gate">
      <p>{r.gate}</p>
      {r.stage_note && <p className="warn small">{r.stage_note}</p>}
      {r.stage === "awaiting_reference" && <ReferenceUpload rid={r.id} reload={reload} />}
      {r.stage === "awaiting_prompt_approval" && (
        <div className="appr-actions">
          <input className="grow" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú cho Hùng (bắt buộc khi yêu cầu sửa)" />
          <Act kind="danger" disabled={!note.trim()} onClick={() => post("prompts", { action: "revise", note }, "Đã gửi yêu cầu sửa cho Hùng")}>Yêu cầu sửa</Act>
          <Act kind="primary" onClick={() => post("prompts", { action: "approve", note }, "Đã duyệt — bộ prompt hoàn thành")}>✓ Duyệt prompt</Act>
        </div>
      )}
      {r.stage === "awaiting_uploads" && (
        <div>
          <ol className="small">
            <li>Mở <a href="https://suno.com/create" target="_blank" rel="noreferrer noopener">suno.com/create</a> → chế độ <b>Custom</b>.</li>
            <li>Với từng bài bên dưới: dán <b>Style</b>, <b>Title</b>, và <b>Lyrics</b> (nhạc không lời: bật <b>Instrumental</b>).</li>
            <li>Nghe các bản Suno tạo ra, chọn bản hay nhất, tải về (MP3 hoặc WAV) rồi tải lên đúng ô của bài đó.</li>
            <li>Tải lên ít nhất một ảnh nền (nên 1920x1080 trở lên), rồi bấm <b>Bắt đầu dựng</b>.</li>
          </ol>
          {d.missing.length > 0 && <p className="small warn">Còn thiếu: {d.missing.join(", ")}</p>}
          <Act kind="primary" disabled={d.missing.length > 0} onClick={() => post("produce", {}, "Tuấn bắt đầu dựng video")}>🎬 Bắt đầu dựng</Act>
        </div>
      )}
      {(r.stage === "awaiting_final" || r.stage === "needs_attention") && (
        <div>
          {r.stage === "awaiting_final" && (
            <div className="appr-actions">
              <span className="grow small muted">Xem video ở mục “Video thành phẩm” bên dưới.</span>
              <Act kind="primary" onClick={() => post("final", { action: "approve", note }, "Đã duyệt — Thảo sẽ viết tổng kết")}>👍 Duyệt video</Act>
            </div>
          )}
          {r.stage === "needs_attention" && d.retry_job && (
            <div className="appr-actions">
              <span className="grow small">Bước lỗi: <b>{d.retry_job.kind_vi}</b> — {(d.retry_job.error || "").slice(0, 200)}</span>
              <Act onClick={() => post("retry", {}, "Đã cho thử lại")}>↻ Thử lại bước này</Act>
            </div>
          )}
          <div className="draft-redo">
            <label className="small">Làm lại
              <select value={step} onChange={(e) => setStep(e.target.value)}>
                <option value="produce">Dựng lại video (sau khi bạn thay nhạc/ảnh nếu cần)</option>
                <option value="write">Viết lại prompt Suno (phải tạo nhạc lại)</option>
              </select>
            </label>
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Cần sửa gì? vd: đổi ảnh nền bài 2, bài 3 nhịp chậm hơn…" />
            <div className="appr-actions">
              <span className="grow small muted">Ghi chú được chuyển cho người phụ trách bước đó.</span>
              <Act kind="danger" disabled={!note.trim()} onClick={() => post("final", { action: "redo", step, note }, "Đã gửi yêu cầu làm lại")}>Gửi yêu cầu làm lại</Act>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function ReferenceUpload({ rid, reload }: { rid: number; reload: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const toast = useToast();
  return (
    <div className="row gap wrap">
      <input type="file" accept="audio/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <Act kind="primary" disabled={!file} onClick={async () => {
        const fd = new FormData(); fd.append("file", file!);
        await api.upload(`/api/requests/${rid}/reference`, fd);
        toast("Đã gửi file bài mẫu — Lan sẽ đo rồi xóa file"); reload();
      }}>Tải bài mẫu lên</Act>
      <span className="small muted">Chỉ dùng để đo, bị xóa ngay sau khi phân tích.</span>
    </div>
  );
}

const TRACK_STATE: Record<string, string> = {
  planned: "⏳ chờ Lan định hướng", writing: "✍️ Hùng đang viết", checking: "🔍 Mai đang soát", revising: "↩️ đang sửa", ok: "✅ đạt", escalated: "⚠️ cần bạn xem",
};

function Prompts({ d }: { d: Json }) {
  const p = d.prompts;
  const yt = p.youtube || {};
  return (
    <Card title={`Prompt Suno — phiên bản ${p.version}`}>
      <div className="tracks">
        {p.tracks.map((t: Json, i: number) => (
          <div key={i} className="track">
            <div className="row wrap gap"><b>{i + 1}. {t.title}</b><CopyBtn text={t.title} label="Tên bài" />
              {t.state && <span className="small muted">{TRACK_STATE[t.state] || t.state}</span>}</div>
            {!t.style ? <div className="small muted">{t.concept ? `Ý tưởng: ${t.concept} — ` : ""}Hùng đang viết…</div> : <>
            <div className="row gap small muted">Style · {t.style.length}/1000 ký tự <CopyBtn text={t.style} label="Style" /></div>
            <pre className="md-pre">{t.style}</pre>
            {t.lyrics
              ? <details><summary className="small">Lời bài hát <CopyBtn text={t.lyrics} label="Lyrics" /></summary><pre className="md-pre">{t.lyrics}</pre></details>
              : <div className="small muted">Không lời — bật <b>Instrumental</b> trên Suno.</div>}
            {t.notes && <div className="small muted">Ghi chú: {t.notes}</div>}
            {t.issues && t.state !== "ok" && <ul className="md-ul small">{t.issues.map((x: string, k: number) => <li key={k}>{x}</li>)}</ul>}
            </>}
          </div>
        ))}
      </div>
      <h4>Gợi ý ảnh nền (tự tạo bằng công cụ bạn thích, hoặc dùng ảnh của bạn)</h4>
      <ul className="md-ul">{p.image_prompts.map((x: string, i: number) => <li key={i}>{x} <CopyBtn text={x} /></li>)}</ul>
      <h4>Nội dung đăng YouTube</h4>
      <div className="row wrap gap"><b>{yt.title}</b><CopyBtn text={yt.title || ""} label="Tiêu đề" /></div>
      <div className="row gap small muted">Mô tả <CopyBtn text={yt.description || ""} label="Mô tả" /></div>
      <pre className="md-pre">{yt.description}</pre>
      <div className="row wrap gap small">Thẻ: {(yt.tags || []).join(", ")} <CopyBtn text={(yt.tags || []).join(", ")} label="Thẻ" /></div>
      {p.tracklist_template && <><h4>Mẫu danh sách bài (video dài)</h4><pre className="md-pre">{p.tracklist_template}</pre>
        <p className="small muted">Mốc thời gian thật được tính khi dựng video dài.</p></>}
      <p className="small muted">Khi đăng YouTube: bật nhãn <b>nội dung chỉnh sửa/tổng hợp (AI)</b>. Dùng thương mại nhạc Suno cần gói trả phí (Pro/Premier) tại thời điểm tạo bài.</p>
    </Card>
  );
}

function Uploads({ d, reload }: { d: Json; reload: () => void }) {
  const r = d.request;
  const toast = useToast();
  const up = async (path: string, file: File, msg: string) => {
    const fd = new FormData(); fd.append("file", file);
    const res = await api.upload(`/api/requests/${r.id}/${path}`, fd);
    toast(res.warning ? `${msg} — ${res.warning}` : msg, res.warning ? "err" : "ok");
    reload();
  };
  return (
    <Card title="Nhạc từ Suno & ảnh nền">
      {d.tracks.map((t: Json) => (
        <div key={t.idx} className="row wrap gap upload-row">
          <b className="upload-name">{t.idx + 1}. {t.title}</b>
          {t.has_audio
            ? <><Badge s="done" label={`${t.audio_name} · ${Math.round(t.duration)}s`} /><audio controls preload="none" src={`/api/requests/${r.id}/tracks/${t.idx}/audio?v=${t.uploaded_at}`} /></>
            : <Badge s="queued" label="chưa có nhạc" />}
          {d.can_upload && <FilePick accept="audio/*" label={t.has_audio ? "Thay file" : "Tải nhạc lên"} onPick={(f) => up(`tracks/${t.idx}/audio`, f, `Đã nhận nhạc bài ${t.idx + 1}`)} />}
        </div>
      ))}
      <h4>Ảnh nền ({d.images.length})</h4>
      <div className="row wrap gap">
        {d.images.map((im: Json, i: number) => (
          <div key={im.id} className="img-tile">
            <img src={im.url} alt={im.filename} />
            <div className="small">{im.filename}{r.output_type !== "long" ? ` · dùng cho bài ${d.tracks.filter((t: Json) => t.idx % d.images.length === i).map((t: Json) => t.idx + 1).join(", ")}` : i === 0 ? " · nền video dài" : ""}</div>
            {d.can_upload && <Act kind="small danger" onClick={async () => { await api.del(`/api/requests/${r.id}/images/${im.id}`); reload(); }}>Gỡ</Act>}
          </div>
        ))}
        {d.can_upload && <FilePick accept="image/png,image/jpeg,image/webp" label="＋ Thêm ảnh nền" onPick={(f) => up("images", f, "Đã thêm ảnh nền")} />}
      </div>
      {!d.can_upload && <p className="small muted">Chỉ thay nhạc/ảnh được khi yêu cầu đang chờ bạn tải lên hoặc chờ duyệt video.</p>}
    </Card>
  );
}

function FilePick({ accept, label, onPick }: { accept: string; label: string; onPick: (f: File) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <label className="btn small file-pick">{busy ? "Đang tải…" : label}
      <input type="file" accept={accept} hidden disabled={busy} onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setBusy(true);
        try { await onPick(f); } catch (err: any) { toast(err.message || String(err), "err"); } finally { setBusy(false); }
      }} />
    </label>
  );
}

const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

function Outputs({ d }: { d: Json }) {
  const o = d.outputs;
  const v = o.verify;
  return (
    <Card title={<>Video thành phẩm {v ? (v.ok ? <Badge s="done" label="qua kiểm tra" /> : <Badge s="failed" label="không qua kiểm tra" />) : <Badge s="running" label="đang kiểm tra" />}</>}>
      <p className="small muted">Thư mục: {o.dir}</p>
      {o.long && (
        <div className="out">
          <h4>Video dài · {fmtDur(o.long.duration)}</h4>
          <div className="draft-grid">
            <video className="player" src={o.long.url} poster={o.long.thumb_url} controls preload="metadata" />
            <div>
              <div className="row gap"><a className="btn small" href={o.long.url} download={o.long.file}>⬇ Tải video</a><a className="btn small" href={o.long.thumb_url} download="thumbnail.png">⬇ Thumbnail</a></div>
              <div className="row gap small">Mô tả kèm danh sách bài <CopyBtn text={o.long.description} label="Mô tả" /></div>
              <pre className="md-pre">{o.long.tracklist}</pre>
            </div>
          </div>
        </div>
      )}
      {o.singles.length > 0 && <h4>Video từng bài ({o.singles.length})</h4>}
      <div className="out-grid">
        {o.singles.map((s: Json) => (
          <div key={s.idx} className="out">
            <video className="player" src={s.url} poster={s.thumb_url} controls preload="none" />
            <div className="row wrap gap small"><b>{s.idx + 1}. {s.title}</b> · {fmtDur(s.duration)}
              <a className="btn small" href={s.url} download={s.file}>⬇ Video</a><a className="btn small" href={s.thumb_url} download={`thumb_${s.idx + 1}.png`}>⬇ Thumbnail</a></div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Timeline({ d }: { d: Json }) {
  const reps: Json[] = d.reports;
  return (
    <Card title={`Dòng thời gian báo cáo (${reps.length})`}>
      <div className="timeline">
        {[...reps].reverse().map((x, i) => (
          <details key={x.id} className={`tl-item ${x.role ? "staff" : "you"}`} open={i < 3}>
            <summary>
              <span className="tl-who">{x.author}</span>
              <b>{x.title}</b>
              <span className="muted small">{fmtTime(x.created_at)}{x.job_id ? ` · việc #${x.job_id}` : ""}</span>
            </summary>
            <Md text={x.body} />
          </details>
        ))}
      </div>
    </Card>
  );
}
