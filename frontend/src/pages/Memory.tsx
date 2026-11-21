import { useState } from "react";
import { api, Json } from "../api";
import { Act, Card, TeamPicker, Toggle, useData, useTeamParam } from "../ui";

export default function Memory() {
  const [team, setTeam] = useTeamParam();
  return (
    <div>
      <h1>Bộ nhớ</h1>
      <p className="muted small">Ba cấp: <b>công ty</b> (quy tắc chung), <b>văn phòng</b> (gu âm nhạc, phong cách prompt, điều cần tránh), <b>công việc</b> (ghi chú của từng nhiệm vụ). Lưu trong dữ liệu ROC, không phụ thuộc lịch sử CLI.</p>
      <MemoryEditor teamId={null} />
      <TeamPicker value={team} onChange={setTeam} />
      {team && <MemoryEditor teamId={team} />}
    </div>
  );
}

export function MemoryEditor({ teamId }: { teamId: number | null }) {
  const { data, reload } = useData<Json>(`/api/memory${teamId ? `?team_id=${teamId}` : ""}`, [teamId]);
  const [txt, setTxt] = useState("");
  const [kind, setKind] = useState("rule");
  const items: Json[] = (teamId ? data?.team : data?.company) || [];
  return (
    <Card title={teamId ? "Bộ nhớ văn phòng" : "Quy tắc công ty"}>
      {items.map((m) => (
        <div key={m.id} className={`mem ${m.active ? "" : "off"}`}>
          <span className="badge gray">{m.kind}</span> {m.content} <span className="muted small">({m.source})</span>
          <Toggle label="dùng" checked={!!m.active} onChange={async (v) => { await api.patch(`/api/memory/${m.id}`, { active: v }); reload(); }} />
          <Act confirm="Xóa mục này?" onClick={async () => { await api.del(`/api/memory/${m.id}`); reload(); }}>Xóa</Act>
        </div>
      ))}
      <div className="row gap">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="rule">quy tắc</option><option value="style">phong cách</option><option value="fact">kiến thức</option><option value="note">ghi chú</option>
        </select>
        <input className="grow" value={txt} onChange={(e) => setTxt(e.target.value)} placeholder="Thêm mục bộ nhớ…" />
        <Act onClick={async () => { await api.post("/api/memory", { scope: teamId ? "team" : "company", team_id: teamId, kind, content: txt }); setTxt(""); reload(); }}>Thêm</Act>
      </div>
    </Card>
  );
}
