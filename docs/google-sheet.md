# Đẩy prompt Suno lên Google Sheet

Mỗi khi Mai soát **đạt** một bài, ROC gửi bài đó lên Google Sheet thành **một dòng**. Nếu bạn duyệt những bài
Mai chưa cho đạt thì các bài đó cũng được gửi lên. ROC dùng Web App của Apps Script, không cần Google Cloud hay API key.

## Cài đặt một lần (khoảng 3 phút)

1. Tạo hoặc mở Google Sheet bạn muốn dùng.
2. Vào **Tiện ích mở rộng → Apps Script**, xóa hết mã có sẵn rồi dán đoạn mã dưới đây vào, sau đó bấm **Lưu**.
3. Bấm **Triển khai → Tùy chọn triển khai mới** (Deploy → New deployment):
   - Loại: **Ứng dụng web** (Web app)
   - Thực thi với tư cách: **Tôi** (Me)
   - Ai có quyền truy cập: **Bất kỳ ai** (Anyone)
   - Bấm **Triển khai**, cấp quyền khi được hỏi, rồi **sao chép URL Web App** (có dạng `https://script.google.com/macros/s/.../exec`).
4. Mở `data\config.json` (chưa có thì chép từ `config.example.json`) và thêm dòng:
   ```json
   "sheets_webhook_url": "https://script.google.com/macros/s/.../exec"
   ```
5. Khởi động lại ROC: chạy `Stop-Mini.bat` rồi `Mini.bat`.

> Ai có URL này thì cũng ghi được dòng vào Sheet của bạn. Đừng chia sẻ URL. Đoạn mã có kiểm tra một mã bí mật
> đơn giản (`SECRET`) để chặn ghi bừa. Nếu muốn dùng, đặt `SECRET` trong mã rồi thêm `?key=<SECRET>` vào cuối URL
> trong config.

## Mã Apps Script

```javascript
const SHEET_NAME = "Prompt Suno";
const SECRET = ""; // để trống = không kiểm tra; nếu đặt thì thêm ?key=... vào URL trong config
const HEADERS = ["Thời gian", "Yêu cầu #", "Tên yêu cầu", "Thể loại", "Phiên bản", "Bài số",
                 "Tên bài", "Style prompt", "Lời", "Có lời?", "Trạng thái"];

function doPost(e) {
  if (SECRET && (e.parameter.key || "") !== SECRET) return out({ ok: false, error: "sai key" });
  const d = JSON.parse(e.postData.contents);
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sh.getLastRow() === 0) sh.appendRow(HEADERS);
  // Cùng yêu cầu + phiên bản + bài số → ghi đè dòng cũ (tránh trùng khi ROC gửi lại)
  const key = [d.request_id, d.version, d.track_no].join("|");
  const rows = sh.getLastRow() > 1 ? sh.getRange(2, 2, sh.getLastRow() - 1, 5).getValues() : [];
  const row = [new Date(d.time * 1000), d.request_id, d.request, d.genre, d.version, d.track_no,
               d.title, d.style, d.lyrics, d.vocals === "vocal" ? "Có lời" : "Không lời",
               d.state === "ok" ? "Mai đạt" : "Bạn duyệt"];
  const i = rows.findIndex(r => [r[0], r[3], r[4]].join("|") === key);
  if (i >= 0) sh.getRange(i + 2, 1, 1, row.length).setValues([row]);
  else sh.appendRow(row);
  return out({ ok: true });
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
```

## Khi không gửi được

- Việc gửi lên Sheet chạy riêng, nên lỗi mạng **không** làm hỏng yêu cầu. ROC tự thử lại tối đa 5 lần, và bạn xem
  được trạng thái ở **Bảng công việc**, mục "Đẩy lên Google Sheet".
- Nếu sửa mã Apps Script, hãy dùng **Quản lý triển khai → Chỉnh sửa → Phiên bản mới** để giữ nguyên URL.
