import { useState } from "react";
import { api, fmtTime, Json } from "../api";
import { Act, Badge, Card, Json_, useData, useRoute } from "../ui";

const STATUS_COLOR: Record<string, string> = {
  queued: "#94a3b8", running: "#38bdf8", awaiting_approval: "#f59e0b", awaiting_connection: "#a78bfa", awaiting_quota: "#a78bfa",
  needs_reconcile: "#ef4444", done: "#22c55e", failed: "#ef4444", cancelled: "#64748b",
};
const EMPTY: Record<string, [string, string]> = {
  "": ["📭", "Chưa có việc nào. Gửi yêu cầu nhạc mới, nhân viên sẽ nhận việc ở đây khi công ty đang chạy."],
  queued: ["☕", "Không có việc nào đang xếp hàng."],
  running: ["💤", "Hiện không có Claude Code / Antigravity CLI hay máy nào đang chạy."],
  awaiting_approval: ["✅", "Không có việc nào chờ bạn duyệt."],
  awaiting_connection: ["🔌", "Không có việc nào bị chặn vì thiếu kết nối."],
  awaiting_quota: ["⏳", "Không có việc nào phải chờ lượt CLI."],
  needs_reconcile: ["👍", "Không có việc nào cần bạn kiểm tra lại."],
  done: ["🗂️", "Chưa có việc nào hoàn thành."],
  failed: ["🎉", "Không có việc nào thất bại."],
  cancelled: ["🗂️", "Không có việc nào bị hủy."],
};
const OPEN = ["queued", "running", "awaiting_approval", "awaiting_connection", "awaiting_quota", "needs_reconcile"];

export default function Jobs() {
  const [, q] = useRoute();
  const [status, setStatus] = useState(q.get("status") || "");
  const [sel, setSel] = useState<number | null>(q.get("id") ? Number(q.get("id")) : null);
  const { data: meta } = useData<Json>("/api/meta");
  const { data: ov } = useData<Json>("/api/overview");
  const { data, reload } = useData<Json[]>(`/api/jobs?${status ? `status=${status}` : ""}`, [status]);
  const counts: Record<string, number> = ov?.jobs || {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const [icon, emptyText] = EMPTY[status] || EMPTY[""];
  return (
    <div className="jobs-page">
      <h1>Bảng công việc <span className="h1-sub">{total} việc · {OPEN.reduce((a, k) => a + (counts[k] || 0), 0)} đang mở</span></h1>
      <div className="status-tabs">
        <button className={`status-tab ${!status ? "on" : ""}`} onClick={() => setStatus("")}>Tất cả <b>{total}</b></button>
        {meta && Object.entries(meta.status_vi).map(([k, v]) => (
          <button key={k} className={`status-tab ${status === k ? "on" : ""} ${counts[k] ? "" : "zero"}`} onClick={() => setStatus(k)}>
            <i style={{ background: STATUS_COLOR[k] }} />{v as string} <b>{counts[k] || 0}</b>
          </button>
        ))}
      </div>
      <div className="split">
        <div className="job-list">
          {!data && <div className="empty-state"><span className="muted">Đang tải…</span></div>}
          {data && !data.length && (
            <div className="empty-state">
              <div className="empty-icon">{icon}</div>
              <b>{status ? `Không có việc “${meta?.status_vi?.[status] || status}”` : "Chưa có việc"}</b>
              <span className="muted">{emptyText}</span>
            </div>
          )}
          {(data || []).map((j) => (
            <div key={j.id} className={`job-row ${sel === j.id ? "sel" : ""}`} onClick={() => setSel(j.id)}>
              <span className="job-bar" style={{ background: STATUS_COLOR[j.status] || "#64748b" }} />
              <div className="job-main">
                <div className="job-top">
                  <b>{j.kind_vi}</b>
                  <span className="job-id">#{j.id}</span>
                  <Badge s={j.status} label={j.status_vi} />
                  {j.attempts > 0 && <span className="muted small">lần {j.attempts}/{j.max_attempts}</span>}
                </div>
                <div className="job-sub">
                  {j.request_id ? <a href={`#/request/${j.request_id}`} onClick={(e) => e.stopPropagation()}>🎵 {j.request_title}</a> : <span>👥 Ban điều hành</span>}
                </div>
                {(j.wait_reason || j.error) && <div className="job-reason">{(j.wait_reason || j.error).slice(0, 220)}</div>}
              </div>
              <div className="job-side">
                <span className="muted small">{fmtTime(j.updated_at)}</span>
                <div className="row" onClick={(e) => e.stopPropagation()}>
                  {OPEN.includes(j.status) &&
                    <Act kind="small" onClick={async () => { await api.post(`/api/jobs/${j.id}/cancel`); reload(); }}>Hủy</Act>}
                  {["failed", "cancelled", "awaiting_connection", "awaiting_quota"].includes(j.status) &&
                    <Act kind="small" onClick={async () => { await api.post(`/api/jobs/${j.id}/retry`); reload(); }}>Thử lại</Act>}
                </div>
              </div>
            </div>
          ))}
        </div>
        {sel && <JobDetail id={sel} onClose={() => setSel(null)} />}
      </div>
    </div>
  );
}

function JobDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, reload } = useData<Json>(`/api/jobs/${id}`, [id]);
  const [log, setLog] = useState<string | null>(null);
  const { data: meta } = useData<Json>("/api/meta");
  if (!data) return null;
  const j = data.job;
  return (
    <Card title={`Việc #${id} — ${meta?.jobs?.[j.kind] || j.kind}`} actions={<button className="btn small" onClick={onClose}>Đóng ✕</button>} className="detail">
      <p><Badge s={j.status} label={meta?.status_vi?.[j.status]} /> {j.wait_reason} {j.error && <span className="err">{j.error}</span>}</p>
      {j.status === "needs_reconcile" && (
        <div className="warn">
          <p><b>Cần đối chiếu:</b> việc bị gián đoạn khi chưa rõ kết quả. Chọn:</p>
          <div className="row gap wrap">
            <Act confirm="Cho chạy lại việc này một lần?" onClick={async () => { await api.post(`/api/jobs/${id}/reconcile`, { resolution: "not_sent" }); reload(); }}>Cho chạy lại</Act>
            <Act onClick={async () => { await api.post(`/api/jobs/${id}/reconcile`, { resolution: "abandon" }); reload(); }}>Bỏ việc này</Act>
          </div>
        </div>
      )}
      <h4>Lần chạy CLI</h4>
      {data.cli_runs.map((r: Json) => (
        <div key={r.id} className="run">
          <div><Badge s={r.status === "ok" ? "done" : r.status === "running" ? "running" : "failed"} label={r.status} /> <b>{r.cli}</b> {r.observed_model} {r.error_kind && <span className="err">{r.error_kind}</span>} {r.resumed ? "(tiếp tục phiên)" : ""}
            <span className="muted small"> {fmtTime(r.started_at)} → {fmtTime(r.ended_at)} · phiên {r.session_id?.slice(0, 8) || "—"}</span></div>
          {r.tool_calls?.length > 0 && <div className="small">Công cụ: {r.tool_calls.map((t: Json, i: number) => <span key={i} className="mr">{t.name}{t.ok === false ? "✗" : "✓"}</span>)}</div>}
          <div className="row gap">
            {["prompt", "stdout", "stderr"].map((w) => <button key={w} className="btn small" onClick={async () => setLog((await api.get(`/api/cli_runs/${r.id}/log/${w}`)).text)}>{w}</button>)}
          </div>
        </div>
      ))}
      {!data.cli_runs.length && <p className="muted small">Việc này không dùng CLI AI.</p>}
      {log !== null && <pre className="log">{log}</pre>}
      {data.handoffs.length > 0 && <><h4>Bàn giao</h4>{data.handoffs.map((h: Json) => <pre key={h.id} className="log">{h.content.slice(0, 3000)}</pre>)}</>}
      <h4>Sự kiện</h4>
      <div className="feed">{data.events.map((e: Json) => <div key={e.id} className={`ev ${e.level}`}><span className="muted">{fmtTime(e.ts)}</span> {e.message}</div>)}</div>
      {j.result && <><h4>Kết quả</h4><Json_ v={JSON.parse(j.result)} /></>}
    </Card>
  );
}
