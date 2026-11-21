# Kết quả nghiệm thu — 29/10/2026

Môi trường: Windows 11 Pro, Python 3.11.9, Node 24.10, FFmpeg 9.0.2, Claude Code 2.1.303 (đăng nhập claude.ai).
Nghiệm thu chạy trên thư mục dữ liệu riêng, không đụng `data\`.

> Ghi chú 18/11/2026: ROC đã chuyển sang mô hình **một văn phòng duy nhất**, luồng dừng ở video hoàn chỉnh trên máy.
> Các phần nghiệm thu dưới đây chỉ giữ những hạng mục còn áp dụng cho sản phẩm hiện tại.

## A. Đã chạy THẬT

| Hạng mục | Bằng chứng |
|---|---|
| Launcher `start-mini.ps1` / `stop-mini.ps1`: khởi động server + worker, mở trình duyệt, dừng gọn (worker lưu trạng thái), khởi động lại và tự khôi phục | Sự kiện "Worker dừng" → "Worker khởi động; khôi phục: 0 … 0 …" |
| Adapter Claude Code: kiểm tra phiên bản, trạng thái đăng nhập (`claude auth status`), chạy không tương tác, stream-json, `--json-schema`, ghi phiên | Nhiều lần chạy thật |
| Nhiệm vụ mẫu: Trưởng nhóm (Claude) đề xuất 3 ý tưởng | 3 ý tưởng tiếng Việt, có điểm, không trùng |
| **Một video đầu-cuối** "Vì sao bầu trời màu xanh…": Nghiên cứu (Claude dùng WebSearch/WebFetch thật — 7/7 dữ kiện có nguồn đã mở: NASA Space Place, HyperPhysics, UC Riverside) → Kịch bản 7 cảnh → Kiểm tra (ĐẠT, có mở lại nguồn để đối chiếu) → Kế hoạch dựng (dùng ảnh + nhạc trong thư viện) → Dựng FFmpeg 53 s → kiểm tra kỹ thuật 7/7 | `docs/nghiem-thu/video-bau-troi-xanh.mp4`, `preview.png`, `thumbnail.png`, `verify.json`, `video-1-artifacts.json` |
| Kiểm tra video thật: h264 1080×1920, AAC 48 kHz, giải mã toàn bộ không lỗi, mức âm thanh, phụ đề trong vùng an toàn, phụ đề thấy được trên khung hình trích xuất | `verify.json` |
| **Giọng đọc OlvRoc**: dựng lại video trên với giọng "Thiện Minh", dùng nguyên kịch bản đã có. Video 42,8 s, kiểm tra kỹ thuật 7/7. Nhận dạng lại bằng faster-whisper cục bộ: nghe được 164/164 từ, khớp kịch bản 95,7 % | `docs/nghiem-thu/video-bau-troi-xanh-giong-olvroc.mp4`, `verify-giong-olvroc.json`, `preview-giong-olvroc.png` |
| Mọi endpoint GET mà giao diện dùng trả 200; SSE hoạt động; POST thiếu `X-Mini` bị chặn 403 | smoke test |

Lỗi phát hiện nhờ chạy thật và **đã sửa** (có kiểm thử hồi quy):
1. Giới hạn độ dài kịch bản tính theo tiếng Anh → từ chối kịch bản tiếng Việt hợp lý. Nay tính ~4,5 âm tiết/giây
   cho tiếng Việt, báo giới hạn cho biên kịch, và đưa lý do bị từ chối lần trước vào lần thử sau.
2. Bộ lập lịch xếp thêm việc đề xuất ý tưởng khi đã có việc đang chạy → nay bỏ qua.
3. Ký hiệu "∝" hiện thành ô vuông → thêm font dự phòng theo ký tự (Segoe UI Symbol, Cambria Math, Segoe UI Emoji).
   *Video nghiệm thu được dựng trước bản sửa này nên cảnh 4 còn ô vuông; dựng lại sẽ hiển thị đúng.*
4. Video không chuyển "Thất bại" khi hết số lần thử → đã sửa.

## B. Chỉ kiểm bằng mock (kiểm thử tự động)

- Hết lượt CLI, lỗi xác thực, chuyển CLI dự phòng có bản bàn giao, JSON không hợp lệ, timeout, hủy tiến trình —
  dùng CLI giả lập qua tiến trình con thật (định dạng sự kiện lấy từ bản CLI thật).
- Hai worker không nhận trùng việc; khôi phục sau mất điện; mỗi nhân viên một việc một lúc; quyền duyệt;
  duyệt bị vô hiệu khi nội dung bị sửa; chỉ có một văn phòng.

## C. Chưa xác minh

- Antigravity CLI chạy thật cho vai Kiểm tra chất lượng (cần bạn đăng nhập `agy`).
- Báo cáo của Trợ lý điều hành chạy thật (logic đã có kiểm thử bằng mock).
- Giao diện được kiểm qua build và API, chưa được bấm thử bằng trình duyệt tự động.

## D. Việc cần bạn làm

1. Đăng nhập Antigravity CLI (README mục 2), rồi *Cài đặt kết nối* → *Chạy thử thật*.
   Nếu không dùng được: đổi vai Kiểm tra chất lượng sang Claude Code.
2. Điền định hướng nội dung trong *Cài đặt văn phòng*; thêm ảnh/clip/nhạc có quyền sử dụng vào thư viện.
3. Chọn giọng OlvRoc (*Cài đặt văn phòng* → *Media & giọng đọc*); nghe thử vài giọng để chọn.
