import { useState } from "react";
import { api, fmtTime, Json } from "../api";
import { Act, Badge, Card, Json_, cliLogin, useData, useToast } from "../ui";

export default function Settings() {
  const { data: h, reload } = useData<Json>("/api/health");
  const [probe, setProbe] = useState<Json>({});
  const toast = useToast();
  if (!h) return <p>Đang kiểm tra công cụ…</p>;
  return (
    <div>
      <h1>Cài đặt kết nối <Act onClick={async () => { await api.get("/api/health?refresh=true"); reload(); }}>Kiểm tra lại</Act></h1>
      <div className="grid2">
        {Object.entries(h.cli).map(([k, c]: [string, Json]) => (
          <Card key={k} title={c.label} actions={<Act onClick={async () => { const r = await api.post(`/api/cli/${k}/probe`); setProbe({ ...probe, [k]: r }); toast(r.ok ? "Chạy thử thành công" : `Chạy thử: ${r.kind}`, r.ok ? "ok" : "err"); reload(); }} title="Chạy một nhiệm vụ thật rất nhỏ (tốn một lượt)">Chạy thử thật</Act>}>
            <p>Cài đặt: {c.installed ? <Badge s="done" label={c.version} /> : <Badge s="failed" label="chưa cài" />}</p>
            <p>Đăng nhập: {cliLogin(h, k) === "?" ? <Badge s="awaiting_approval" label="chưa xác minh" /> : c.logged_in ? <Badge s="done" label="có" /> : <Badge s="failed" label="chưa" />}
              {k === "claude" && c.auth_method && <span className="small"> ({c.auth_method}{c.subscription ? `, gói ${c.subscription}` : ""})</span>}
              {k === "antigravity" && <span className="small muted"> {c.last_verified ? `lần chạy thật gần nhất ${fmtTime(c.last_verified.ts)}: ${c.last_verified.ok ? "thành công" : c.last_verified.kind}` : c.note}</span>}</p>
            {c.billing_problem && <p className="warn small">{c.billing_problem}</p>}
            {c.uses_api_key && !c.billing_problem && <p className="warn small">CLI đang cấu hình dùng API key — có thể phát sinh phí. ROC được thiết kế cho đăng nhập tài khoản.</p>}
            {c.env_removed?.length > 0 && <p className="small">ROC gỡ biến môi trường {c.env_removed.join(", ")} khi chạy CLI để tránh chuyển sang API trả phí.</p>}
            {c.blocked_until && <p className="warn">Hết lượt — chờ tới {fmtTime(c.blocked_until)}</p>}
            {probe[k] && <Json_ v={probe[k]} />}
            <details><summary>Cách đăng nhập</summary>
              {k === "claude" ? <ol className="small"><li>Mở PowerShell, chạy <code>claude</code>.</li><li>Gõ <code>/login</code>, chọn tài khoản Claude (gói Pro/Max/Team), hoàn tất trên trình duyệt.</li><li>Thoát, bấm “Kiểm tra lại”.</li></ol>
                : <ol className="small">
                  <li>Cài bản chính thức (Google): mở PowerShell, chạy <code>irm https://antigravity.google/cli/install.ps1 | iex</code>. File chạy nằm ở <code>{"%LOCALAPPDATA%\\agy\\bin"}</code>.</li>
                  <li>Chạy <code>agy</code>. Lần đầu CLI mở trình duyệt để bạn <b>đăng nhập tài khoản Google</b> (gói Free/Pro/Ultra), rồi hỏi màu, kiểu hiển thị và tin cậy thư mục.</li>
                  <li>Trong <code>agy</code>, gõ <code>/config</code> và kiểm tra <b>Use G1 Credits</b> đang <b>tắt</b> (mặc định tắt). Bật mục này thì CLI dùng credit trả phí khi hết hạn mức — ROC sẽ không chạy.</li>
                  <li>Thoát (<code>/exit</code>), quay lại đây bấm “Chạy thử thật”.</li>
                  <li>Antigravity CLI không có lệnh kiểm tra đăng nhập, nên ROC xác minh bằng lần chạy thật gần nhất. Gemini CLI không còn dùng được với tài khoản cá nhân từ 18/06/2026.</li></ol>}
            </details>
          </Card>
        ))}
      </div>
      <div className="grid2">
        <Card title="Âm thanh & video">
          <p>FFmpeg (đo độ lớn, chuẩn hóa, dựng video): {h.ffmpeg.installed ? <Badge s="done" label={h.ffmpeg.version?.slice(0, 40)} /> : <Badge s="failed" label="chưa cài" />}</p>
          <p>yt-dlp (tải âm thanh bài mẫu để đo): {h.ytdlp?.installed ? <Badge s="done" label={h.ytdlp.version} /> : <Badge s="failed" label="chưa cài" />}</p>
          <p className="small muted">Suno chưa có API công khai: bạn tạo nhạc trên suno.com bằng prompt văn phòng viết, rồi tải file lên trang yêu cầu.</p>
          <h4>Giới hạn vận hành</h4>
          <Json_ v={h.limits} />
          <p className="small muted">Thư mục dữ liệu: {h.data_dir}</p>
        </Card>
      </div>
    </div>
  );
}
