// Nhân sự cố định của công ty: mỗi vai một người, làm chung trong một văn phòng (mỗi lúc một việc).
import { useState } from "react";
import { api, fmtTime, Json } from "../api";
import { Act, Badge, CLI_NAME, Card, Toggle, useData, useToast } from "../ui";

export default function Staff() {
  const { data, reload } = useData<Json[]>("/api/employees");
  if (!data) return <p>Đang tải…</p>;
  return (
    <div>
      <h1>Nhân viên</h1>
      <p className="muted small">Văn phòng có một bộ nhân viên cố định. Mỗi người làm một việc tại một thời điểm;
        việc khác cùng vai sẽ xếp hàng chờ. Nhân viên chỉ chạy khi có nhiệm vụ.
        Model để trống = dùng mặc định của CLI theo tài khoản của bạn.</p>
      {data.map((e) => <EmployeeCard key={e.id} e={e} reload={reload} />)}
    </div>
  );
}

export function EmployeeCard({ e, reload }: { e: Json; reload: () => void }) {
  const [f, setF] = useState({ name: e.name, cli: e.cli, model: e.model || "", fallback_cli: e.fallback_cli || "", instructions: e.instructions, allowed_tools: e.allowed_tools });
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const tool = (x: string) => setF({ ...f, allowed_tools: f.allowed_tools.includes(x) ? f.allowed_tools.filter((y: string) => y !== x) : [...f.allowed_tools, x] });
  const busy = e.status === "working" ? `đang làm ${e.job_kind_vi || e.job_kind || ""}` : "chờ lượt CLI";
  return (
    <Card title={<>{e.name} <span className="muted small">{e.role_vi} · {e.id}</span></>} actions={
      <><Badge s={e.status === "idle" ? "queued" : "running"} label={e.status === "idle" ? "rảnh" : busy} />
        <button className="btn" onClick={() => setOpen(!open)}>{open ? "Đóng" : "Sửa"}</button></>
    }>
      <div className="row gap wrap small">
        <span>CLI: <b>{CLI_NAME[e.cli] || e.cli}</b></span>
        <span>Model: {e.model || <i>mặc định CLI</i>}</span>
        <span>Dự phòng: {e.fallback_cli || "không"}</span>
        <span>Công cụ: {e.allowed_tools.join(", ") || "không"}</span>
      </div>
      <div className="small muted">Đầu vào: {e.io_spec.inputs} · Đầu ra: {e.io_spec.outputs} · Hoàn thành khi: {e.io_spec.done}</div>
      {e.last_runs?.length > 0 && <div className="small">Lần chạy gần nhất: {e.last_runs.map((r: Json) => (
        <span key={r.id} className="mr"><Badge s={r.status === "ok" ? "done" : r.status === "running" ? "running" : "failed"} label={`${r.cli} ${r.error_kind || r.status}`} /> {r.observed_model || ""} {fmtTime(r.started_at)}</span>))}</div>}
      {open && (
        <div className="form">
          <label>Tên nhân viên <input value={f.name} maxLength={40} onChange={(x) => setF({ ...f, name: x.target.value })} /></label>
          <label>CLI <select value={f.cli} onChange={(x) => setF({ ...f, cli: x.target.value })}><option value="claude">Claude Code</option><option value="antigravity">Antigravity CLI</option></select></label>
          <label>Model (tùy chọn, phải là model CLI/tài khoản của bạn hỗ trợ) <input value={f.model} onChange={(x) => setF({ ...f, model: x.target.value })} placeholder="để trống = mặc định" /></label>
          <label>CLI dự phòng (chỉ dùng khi bật "Cho phép CLI dự phòng") <select value={f.fallback_cli} onChange={(x) => setF({ ...f, fallback_cli: x.target.value })}><option value="">Không</option><option value="claude">Claude Code</option><option value="antigravity">Antigravity CLI</option></select></label>
          <div>Công cụ được phép: {["web_search", "web_fetch", "read"].map((x) => <Toggle key={x} label={x} checked={f.allowed_tools.includes(x)} onChange={() => tool(x)} />)}</div>
          <label>Hướng dẫn làm việc <textarea rows={4} value={f.instructions} onChange={(x) => setF({ ...f, instructions: x.target.value })} /></label>
          <Act kind="primary" onClick={async () => { await api.patch(`/api/employees/${e.id}`, f); toast("Đã lưu"); setOpen(false); reload(); }}>Lưu</Act>
        </div>
      )}
    </Card>
  );
}
