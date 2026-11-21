// Hồ sơ một nhân viên: việc của vai này (hủy/thử lại), cấu hình (sửa được), lịch sử chạy CLI và nhật ký.
import { api, fmtTime, Json } from "../api";
import { Act, Badge, Card, useData } from "../ui";
import { EmployeeCard } from "./Staff";

export function EmployeeView({ id }: { id: string }) {
  const { data, reload } = useData<Json>(`/api/office/employee/${id}`, [id]);
  const { data: jobs, reload: rj } = useData<Json[]>("/api/jobs?limit=150");
  if (!data) return <p>Đang tải…</p>;
  const e = data.employee;
  const kinds = data.team_role_kinds;
  const mine = (jobs || []).filter((j) => (kinds || []).includes(j.kind) && !["done", "cancelled"].includes(j.status)).slice(0, 10);
  return (
    <div>
      <h2>{e.name} <span className="muted small">{e.role_vi} · {e.id}</span></h2>
      <Card title="Việc của nhân viên này">
        {mine.map((j) => (
          <div key={j.id} className="row gap wrap small job-line">
            <Badge s={j.status} label={j.status_vi} /> <b>{j.kind_vi}</b>
            {j.request_id && <a href={`#/request/${j.request_id}`}>🎵 {j.request_title}</a>}
            <span className="muted">{j.wait_reason || j.error?.slice(0, 120)}</span>
            {["queued", "running", "awaiting_approval", "awaiting_connection", "awaiting_quota", "needs_reconcile"].includes(j.status) &&
              <Act onClick={async () => { await api.post(`/api/jobs/${j.id}/cancel`); rj(); }}>Hủy</Act>}
            {["failed", "cancelled", "awaiting_connection", "awaiting_quota"].includes(j.status) &&
              <Act onClick={async () => { await api.post(`/api/jobs/${j.id}/retry`); rj(); }}>Thử lại</Act>}
            {j.status === "needs_reconcile" && <a href="#/jobs?status=needs_reconcile">Đối chiếu</a>}
          </div>
        ))}
        {jobs && !mine.length && <p className="muted small">Không có việc đang mở.</p>}
      </Card>
      <EmployeeCard e={{ ...e, last_runs: data.runs }} reload={reload} />
      <Card title="Lần chạy CLI gần đây">
        {data.runs.map((r: Json) => <div key={r.id} className="small"><Badge s={r.status === "ok" ? "done" : r.status === "running" ? "running" : "failed"} label={r.error_kind || r.status} /> {r.cli} {r.observed_model || ""} · {fmtTime(r.started_at)}</div>)}
        {!data.runs.length && <p className="small muted">Chưa chạy lần nào.</p>}
      </Card>
      <Card title="Nhật ký">
        <div className="feed">{data.events.map((ev: Json) => <div key={ev.id} className={`ev ${ev.level}`}><span className="muted">{fmtTime(ev.ts)}</span> {ev.message}</div>)}</div>
      </Card>
    </div>
  );
}
