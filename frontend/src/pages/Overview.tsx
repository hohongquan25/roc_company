import { api, fmtTime, Json } from "../api";
import { Act, Badge, CLI_NAME, Card, Toggle, useData, useLive, useToast } from "../ui";

export default function Overview() {
  const { data: ov, reload } = useData<Json>("/api/overview");
  const { events } = useLive();
  const { data: recent } = useData<Json[]>("/api/events?limit=40");
  const toast = useToast();
  if (!ov) return <p>Đang tải…</p>;
  const c = ov.company;
  const o = ov.office;
  const flag = async (k: string, v: boolean) => { await api.post("/api/company/flags", { [k]: v }); reload(); };
  const feed = events.length ? events : recent || [];
  return (
    <div>
      <h1>Tổng quan công ty</h1>
      <Card title="Điều khiển" actions={
        <>
          <Act kind="primary" disabled={c.company_running} onClick={async () => { await api.post("/api/company/start"); toast("Đã bắt đầu công ty"); reload(); }}>Bắt đầu công ty</Act>
          <Act disabled={!c.company_running} onClick={async () => { await api.post("/api/company/pause"); reload(); }}>Tạm dừng công ty</Act>
          <Act kind="danger" confirm="Hủy ngay mọi việc đang chạy (tiến trình CLI sẽ bị dừng)?" onClick={async () => { await api.post("/api/company/emergency_stop"); reload(); }}>Dừng khẩn cấp</Act>
          <a className="btn" href="#/approvals">Cần bạn xử lý ({ov.pending_approvals})</a>
          <a className="btn" href="#/requests?new=1">＋ Yêu cầu mới</a>
        </>
      }>
        <div className="row wrap gap">
          <Toggle label="Cho phép CLI dự phòng (khi nhân viên có cấu hình)" checked={c.allow_cli_fallback} onChange={(v) => flag("allow_cli_fallback", v)}
            hint="Mặc định tắt: khi hết lượt thì lưu tiến độ và chờ, không tự chuyển công cụ" />
        </div>
        <p className="muted small">Nhân viên chỉ làm theo <a href="#/requests">yêu cầu của bạn</a> (link bài mẫu + thể loại). Bạn duyệt prompt Suno,
          tự tạo nhạc trên Suno rồi tải lên (Suno chưa có API công khai), văn phòng dựng video và kiểm tra; video thành phẩm nằm trên máy.
          Claude Code và Antigravity CLI chạy trên máy nhưng model suy luận trên cloud của nhà cung cấp.</p>
        {!ov.workers.length && <p className="warn">Worker nền chưa chạy — công việc sẽ không được thực hiện. Hãy mở ROC bằng <code>Mini.bat</code>.</p>}
        {ov.providers.filter((p: Json) => p.blocked_until && p.blocked_until * 1000 > Date.now()).map((p: Json) => (
          <p key={p.cli} className="warn">{CLI_NAME[p.cli] || p.cli} đang hết lượt — chờ đến {fmtTime(p.blocked_until)}. Công việc đã được lưu.</p>
        ))}
      </Card>
      <div className="grid4">
        {Object.entries(ov.jobs).map(([k, n]) => (
          <a key={k} className="stat" href={`#/jobs?status=${k}`}><span>{ov.status_vi[k]}</span><b>{n as number}</b></a>
        ))}
      </div>
      <div className="grid2">
        <Card title="Văn phòng" actions={<Act onClick={async () => { await api.patch("/api/workspace", { paused: !o.paused }); reload(); }}>{o.paused ? "▶ Cho văn phòng chạy tiếp" : "⏸ Tạm dừng văn phòng"}</Act>}>
          <div className="team-row">
            {o.paused && <Badge s="failed" label="tạm dừng" />}
            <span className="muted small">yêu cầu đang làm {o.requests_active} · tổng {o.requests_total} · chờ bạn {o.waiting_for_you} · việc mở {o.open_jobs}</span>
            <div className="small">{o.working.length ? o.working.map((w: Json) => <div key={w.id}>⚙ {w.name}: {w.kind || "đang chờ lượt"} ({w.cli})</div>) : <span className="muted">Không ai đang làm việc</span>}</div>
          </div>
        </Card>
        <Card title="Hoạt động thật (sự kiện)">
          <div className="feed">
            {feed.slice(0, 40).map((e: Json) => (
              <div key={e.id} className={`ev ${e.level}`}><span className="muted">{fmtTime(e.ts)}</span> {e.message}</div>
            ))}
            {!feed.length && <p className="muted">Chưa có sự kiện.</p>}
          </div>
        </Card>
      </div>
    </div>
  );
}
