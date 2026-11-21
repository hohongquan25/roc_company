// Cần bạn xử lý: các yêu cầu đang dừng ở cổng chờ bạn (duyệt prompt, tạo nhạc & tải lên, duyệt video, sự cố).
import { Json } from "../api";
import { useData } from "../ui";
import { RequestRow } from "./Requests";

export default function Approvals() {
  const { data } = useData<Json[]>("/api/inbox");
  return (
    <div className="appr-page">
      <h1>Cần bạn xử lý <span className="h1-sub">{data ? `${data.length} yêu cầu đang chờ bạn` : ""}</span></h1>
      <div className="appr-list">
        {!data && <div className="empty-state"><span className="muted">Đang tải…</span></div>}
        {data && !data.length && (
          <div className="empty-state">
            <div className="empty-icon">✅</div>
            <b>Không có gì chờ bạn</b>
            <span className="muted">Khi nhân viên cần bạn duyệt prompt, tạo nhạc trên Suno, duyệt video hoặc xử lý sự cố, yêu cầu sẽ hiện ở đây và trên khay bàn giám đốc.</span>
          </div>
        )}
        {(data || []).map((r) => <RequestRow key={r.id} r={r} />)}
      </div>
    </div>
  );
}
