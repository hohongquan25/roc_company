import { useState } from "react";
import { api, fmtTime, Json } from "../api";
import { Act, Card, useData, useToast } from "../ui";

export default function Reports() {
  const { data: reports } = useData<Json[]>("/api/reports");
  const [level, setLevel] = useState("");
  const { data: events } = useData<Json[]>(`/api/events?limit=300${level ? `&level=${level}` : ""}`, [level]);
  const toast = useToast();
  return (
    <div>
      <h1>Báo cáo & nhật ký</h1>
      <Card title="Báo cáo của trợ lý điều hành" actions={<Act kind="primary" onClick={async () => { await api.post("/api/reports", { days: 1 }); toast("Đã giao trợ lý viết báo cáo (chạy khi công ty hoạt động)"); }}>Tạo báo cáo hôm nay</Act>}>
        {(reports || []).map((r) => (
          <div key={r.id} className="report">
            <h4>{r.content.headline} <span className="muted small">{fmtTime(r.created_at)}</span></h4>
            {r.content.sections.map((s: Json, i: number) => <div key={i}><b>{s.title}</b><p>{s.body}</p></div>)}
            {r.content.decisions_needed?.length > 0 && <div className="warn"><b>Cần bạn quyết định:</b><ul>{r.content.decisions_needed.map((d: string, i: number) => <li key={i}>{d}</li>)}</ul></div>}
          </div>
        ))}
        {reports && !reports.length && <p className="muted">Chưa có báo cáo.</p>}
      </Card>
      <Card title="Nhật ký sự kiện" actions={<select value={level} onChange={(e) => setLevel(e.target.value)}><option value="">mọi mức</option><option value="info">info</option><option value="warn">warn</option><option value="error">error</option></select>}>
        <div className="feed tall">{(events || []).map((e) => (
          <div key={e.id} className={`ev ${e.level}`}><span className="muted">{fmtTime(e.ts)}</span> <span className="muted small">{e.type}</span> {e.message}
            {e.job_id && <a className="small" href={`#/jobs`}> việc #{e.job_id}</a>}</div>
        ))}</div>
      </Card>
    </div>
  );
}
