# ROC — văn phòng làm nhạc AI (Suno) và video nhạc, chạy trên máy của bạn

ROC là ứng dụng web chạy trên máy Windows (localhost), mô phỏng **một văn phòng duy nhất** làm nhạc AI bằng Suno
và dựng video nhạc để bạn đăng YouTube. Văn phòng có 5 nhân viên AI cố định — Trưởng nhóm, Nghiên cứu, Biên tập,
Sản xuất, Kiểm tra chất lượng — và một Trợ lý điều hành (AI) viết tổng kết.

Nhân viên **chỉ làm theo yêu cầu của bạn**: bạn gửi link bài mẫu + thể loại, trưởng nhóm lập kế hoạch và phân công,
**mỗi bước đều có báo cáo** trên dòng thời gian của yêu cầu. Kết quả cuối cùng là **file video + thumbnail trên máy**,
kèm tiêu đề/mô tả/thẻ để bạn tự đăng.

> Claude Code và Antigravity CLI chạy trên máy, nhưng **model suy luận chạy trên cloud** của Anthropic/Google.
> ROC dùng đăng nhập tài khoản của từng CLI, không cần API key, không tự mua thêm lượt.

---

## 1. Cài đặt và chạy

Yêu cầu: Windows 10/11, Python 3.11+, Node.js 20+, FFmpeg trong PATH.

```powershell
# lần đầu (hoặc sau khi cập nhật mã nguồn) — cài cả librosa (đo nhạc) và yt-dlp (tải bài mẫu)
powershell -ExecutionPolicy Bypass -File scripts\setup-mini.ps1
```

- **Mở ROC:** nhấp đúp `Mini.bat` → khởi động backend + worker nền, mở `http://127.0.0.1:8765`.
- **Dừng ROC:** nhấp đúp `Stop-Mini.bat`.
- Đóng tab trình duyệt **không** dừng worker. Tắt máy thì worker dừng; lần mở sau ROC tự đối chiếu và làm tiếp.
- ROC chỉ có **một** văn phòng ("Văn phòng ROC"), tự tạo lần đầu mở; không có cài đặt nội dung mặc định —
  mọi thứ đi từ yêu cầu bạn gửi.
- Dữ liệu ở `data\`: cơ sở dữ liệu `mini.db`, thư mục làm việc `teams\van-phong-roc\requests\<số yêu cầu>\`
  (nhạc bạn tải lên, ảnh nền, video thành phẩm), nhật ký `logs\`. Làm lại từ đầu: dừng ROC, xóa `data\mini.db*`
  và `data\teams\`, mở lại.
- Đổi mặc định (khung hình, âm lượng mục tiêu…): chép `config.example.json` thành `data\config.json`.

## 2. Luồng một yêu cầu nhạc

```
Bạn gửi yêu cầu (link bài mẫu + thể loại + ghi chú + kiểu video + số bài + có lời/không lời)
 ┌ Minh (Trưởng nhóm)   lập kế hoạch, phân công và DÀN BÀI (tên + ý tưởng từng bài,      [báo cáo]
 │                      gợi ý ảnh nền, tiêu đề/mô tả/thẻ YouTube)
 └ Lan (Nghiên cứu)     CHẠY CÙNG LÚC: tải âm thanh bài mẫu bằng yt-dlp, ĐO bằng chương trình  [báo cáo]
                        (BPM, tông, độ dài, LUFS, đường năng lượng, độ sáng âm sắc) rồi XÓA file
 → Dây chuyền từng bài, bài nào xong khâu trước là chuyển ngay sang khâu sau:
     Lan   định hướng riêng cho bài (nhịp, tông, nhạc cụ chính, điểm khác các bài khác)   [báo cáo]
     Hùng  viết prompt Suno cho bài (≤ 1000 ký tự, không tên nghệ sĩ), lời gốc nếu có lời [báo cáo]
     Mai   soát bài (chương trình + AI); cần sửa thì trả Hùng, tối đa 2 vòng mỗi bài       [báo cáo]
     ĐẠT → bài được đẩy lên Google Sheet (xem docs/google-sheet.md)
   (Mai soát bài 1 trong lúc Hùng viết bài 2 và Lan định hướng bài 3.)
 → Mọi bài đạt → yêu cầu HOÀN THÀNH.
   Có bài Mai chưa cho đạt sau 2 vòng → ✋ BẠN duyệt (hoàn thành) hoặc yêu cầu sửa kèm ghi chú.
```

**Dựng video (việc riêng, chỉ khi bạn cần):** ở yêu cầu đã hoàn thành, bấm **Dựng video** →
✋ bạn tạo nhạc trên Suno, tải nhạc và ảnh nền lên, bấm "Bắt đầu dựng" → Tuấn chuẩn hóa −14 LUFS, dựng video
1920x1080 và thumbnail → Mai kiểm tra file thật → ✋ bạn duyệt video → Thảo viết tổng kết.

- **Suno chưa có API công khai** (chỉ có chương trình đối tác giới hạn), nên bước tạo nhạc là bạn làm tay trên
  suno.com. ROC không dùng các "Suno API" không chính thức (vi phạm điều khoản, dễ bị khóa tài khoản).
- Không tải được bài mẫu qua link (bị chặn, bài quá dài, link danh sách phát…) → yêu cầu dừng ở
  "Chờ bạn tải file bài mẫu"; bạn tải file âm thanh lên, Lan đo rồi xóa file.
- Không có link bài mẫu → nhân viên làm theo thể loại và ghi chú.
- Một bước lỗi hẳn (sau các lần thử) → yêu cầu chuyển "Cần bạn xử lý" kèm báo cáo lỗi; bấm *Thử lại bước này*.
- Video dài dùng ảnh nền đầu tiên; video từng bài dùng ảnh nền xoay vòng theo thứ tự bài.

## 3. Bản quyền và đăng YouTube — lưu ý

- Bài mẫu chỉ để **học phong cách**. ROC xóa âm thanh bài mẫu ngay sau khi đo, chỉ giữ con số. Prompt và nội dung đăng
  không được chứa tên nghệ sĩ/bài hát/kênh của bài mẫu — Mai kiểm tra bằng chương trình và AI.
- **Dùng thương mại nhạc Suno** (bật kiếm tiền YouTube) cần bài được tạo khi bạn đang dùng **gói trả phí** (Pro/Premier).
- Khi đăng: bật nhãn **nội dung chỉnh sửa/tổng hợp** (AI) của YouTube. Kênh đăng hàng loạt video na ná nhau dễ bị
  từ chối kiếm tiền vì nội dung lặp lại — mỗi video nên có ảnh, chủ đề, tuyển chọn riêng.

## 4. Đăng nhập hai CLI (bạn tự làm bằng công cụ chính thức)

**Claude Code:**
1. PowerShell: `claude` → gõ `/login` → chọn tài khoản Claude → hoàn tất trên trình duyệt.
2. Trong ROC: *Cài đặt kết nối* → *Kiểm tra lại*.

**Antigravity CLI** (`agy`): vai Kiểm tra chất lượng mặc định dùng Antigravity CLI.
Antigravity dừng cả lượt nếu tự gọi lệnh terminal (bị chặn ở chế độ không tương tác) — ROC nhắc nó không làm vậy.
1. Cài bản chính thức: PowerShell `irm https://antigravity.google/cli/install.ps1 | iex` (file chạy ở `%LOCALAPPDATA%\agy\bin`).
2. Chạy `agy` → trình duyệt mở để đăng nhập tài khoản Google → chọn màu, kiểu hiển thị, tin cậy thư mục.
3. Trong `agy`: `/config` → kiểm tra **Use G1 Credits** đang **tắt** (mặc định). Nếu bật, CLI dùng credit trả phí
   khi hết hạn mức — ROC phát hiện và không chạy. ROC cũng không chạy khi `modelProvider` là `gemini` (API key).
4. Thoát, trong ROC: *Cài đặt kết nối* → *Chạy thử thật*. Antigravity CLI không có lệnh kiểm tra đăng nhập, nên
   ROC hiện "chưa xác minh" cho tới khi có một lần chạy thật.

Không muốn dùng Antigravity: trang *Nhân viên* → đổi vai đó sang Claude Code.

ROC luôn gỡ các biến `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `AGY_ADC_AUTH`… khỏi môi trường khi
chạy CLI để CLI không vô tình chuyển sang API trả phí.

## 5. Giao diện

**Văn phòng 2D (mặc định):** văn phòng pixel-art, nhân viên di chuyển theo trạng thái thật (ngồi gõ phím khi CLI đang
chạy, đứng ở máy dựng khi đang dựng, mang tài liệu sang bàn người làm bước tiếp theo khi một bước xong, ra pantry/sofa
khi rảnh). Khay hồ sơ trên bàn giám đốc = số yêu cầu đang chờ bạn.

| Đồ vật | Mở |
|---|---|
| Bàn giám đốc | Cần bạn xử lý |
| Quầy lễ tân · bảng trắng góc họp | Gửi yêu cầu nhạc mới |
| Biển ROC · tủ hồ sơ · máy dựng | Danh sách yêu cầu |
| Tủ video thành phẩm | Yêu cầu đã hoàn thành |
| Phòng kỹ thuật (Claude, Antigravity, FFmpeg, yt-dlp, Worker) | Cài đặt kết nối |
| Bảng việc · góc họp · tủ sách | Bảng công việc · Báo cáo & nhật ký · Bộ nhớ |

**Trang một yêu cầu:** khung "Việc của bạn" (duyệt prompt / tải nhạc & ảnh / duyệt video / xử lý sự cố), prompt Suno
có nút chép (Style, Title, Lyrics), tiêu đề/mô tả/thẻ YouTube, ô tải nhạc từng bài và ảnh nền, video thành phẩm
(xem, tải về, thumbnail, mô tả kèm danh sách bài), và **dòng thời gian báo cáo** của từng nhân viên.

**Nút điều khiển:** Bắt đầu / Tạm dừng / Dừng khẩn cấp công ty, Tạm dừng văn phòng, Hủy yêu cầu,
Thử lại / Hủy công việc.

## 6. Thiết kế an toàn chính

- Prompt chỉ đi qua **stdin**; CLI được gọi trực tiếp, không qua shell. Không bật `--dangerously-skip-permissions`.
- Thông tin trang của bài mẫu (tiêu đề, kênh…) được bọc trong khối "DỮ LIỆU KHÔNG TIN CẬY" với ranh giới ngẫu nhiên.
- Link bài mẫu chỉ nhận `http/https`; bài mẫu dài quá 15 phút bị bỏ qua (`reference_max_seconds`).
- File tải lên được kiểm tra (âm thanh đọc được, ảnh mở được, tối đa 300 MB); mọi file nằm trong thư mục văn phòng,
  đường dẫn ra ngoài bị chặn.
- Hàng đợi có lease; khởi động lại thì việc "đang chạy" của worker đã chết được đưa lại hàng đợi. Hết lượt CLI → lưu
  tiến độ, chờ tới giờ reset. Chuyển CLI dự phòng chỉ khi bạn bật *Cho phép CLI dự phòng* và nhân viên có cấu hình.
- API chỉ nghe `127.0.0.1`, kiểm tra Host và yêu cầu header `X-Mini` cho mọi thao tác ghi.

## 7. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| "Worker chưa chạy" | Mở bằng `Mini.bat`; xem `data\logs\worker.err.log`. |
| Việc "Chờ kết nối" — CLI chưa đăng nhập | Đăng nhập CLI (mục 4), rồi *Thử lại* trong Bảng công việc. |
| Việc "Chờ lượt sử dụng" | CLI hết lượt; ROC tự chạy lại sau giờ reset. |
| "Chờ bạn tải file bài mẫu" | Link bị chặn/không hỗ trợ — tải file âm thanh bài mẫu lên trang yêu cầu. |
| Kiểm tra video không đạt | Đọc báo cáo của Mai; thay nhạc/ảnh nếu cần rồi chọn *Dựng lại video*. |
| Cổng 8765 bận | Đặt `MINI_PORT` hoặc `port` trong `data\config.json`. |

## 8. Kiểm thử

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q
```

AI trong kiểm thử là **CLI giả lập (mock)** chạy qua đúng adapter và tiến trình con thật; đo âm thanh và dựng video
dùng **librosa/FFmpeg thật trên file thật** (âm thử tạo bằng FFmpeg, không cần mạng). Phạm vi: tạo yêu cầu, đo bài mẫu
và xóa file, chờ file khi không tải được link, vòng sửa Biên tập ↔ Kiểm tra, cổng duyệt và tải lên, chuẩn hóa −14 LUFS,
dựng video từng bài + video dài, kiểm tra video, dòng thời gian báo cáo, lỗi → cần bạn xử lý → thử lại, hàng đợi,
hết lượt/lỗi CLI.



