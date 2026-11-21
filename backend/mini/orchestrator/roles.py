"""Vai trò, hướng dẫn làm việc mặc định và schema đầu ra cho luồng nhạc Suno → video nhạc."""
from __future__ import annotations

ROLES = {
    "lead": "Trưởng nhóm",
    "research": "Nghiên cứu",
    "writer": "Biên tập",
    "production": "Sản xuất",
    "qa": "Kiểm tra chất lượng",
}
EXEC_ROLE = "exec_assistant"

# Nhân sự cố định của công ty: mỗi vai một người, làm chung trong một văn phòng (mỗi lúc một việc).
STAFF_ID = {**{r: f"NV-{r.upper()}" for r in ROLES}, EXEC_ROLE: "EXEC-ASSIST"}
STAFF_NAME = {
    "lead": "Minh", "research": "Lan", "writer": "Hùng", "production": "Tuấn",
    "qa": "Mai", EXEC_ROLE: "Thảo",
}

DEFAULT_CLI = {
    "lead": "claude", "research": "claude", "writer": "claude", "production": "claude",
    "qa": "antigravity", EXEC_ROLE: "claude",
}

# công cụ logic được phép (adapter ánh xạ sang tên thật của từng CLI). Số đo âm thanh do chương trình
# tính sẵn; nhân viên chỉ diễn giải, nên không vai nào bắt buộc phải tìm web.
DEFAULT_TOOLS = {
    "lead": [], "research": [], "writer": [], "production": [], "qa": [], EXEC_ROLE: [],
}

IO_SPEC = {
    "lead": {
        "inputs": "Yêu cầu của bạn: link bài mẫu, thể loại, ghi chú, kiểu video, số bài, có lời hay không lời",
        "outputs": "Kế hoạch thực hiện và phân công cho từng người (báo cáo)",
        "done": "Mỗi bước có người phụ trách, đầu ra rõ ràng, nêu rủi ro (bản quyền, chất lượng)",
    },
    "research": {
        "inputs": "Số đo thật của bài mẫu (BPM, tông, độ dài, độ lớn LUFS, đường năng lượng) + thể loại",
        "outputs": "Báo cáo phong cách: nhịp độ, tông, nhạc cụ, cấu trúc, cảm xúc, gợi ý cho prompt",
        "done": "Chỉ diễn giải từ số đo và yêu cầu; không chép giai điệu, lời hay tên nghệ sĩ",
    },
    "writer": {
        "inputs": "Kế hoạch của trưởng nhóm, báo cáo nghiên cứu, ghi chú sửa (nếu có)",
        "outputs": "Prompt phong cách Suno (≤1000 ký tự), lời gốc (nếu có lời), tên bài, gợi ý ảnh nền, "
                   "tiêu đề/mô tả/thẻ YouTube, mẫu danh sách bài cho video dài",
        "done": "Đúng số bài; không tên nghệ sĩ/bài hát có thật; lời là sáng tác mới",
    },
    "production": {
        "inputs": "File nhạc bạn tạo trên Suno + ảnh nền bạn tải lên",
        "outputs": "Âm thanh chuẩn hóa -14 LUFS, video từng bài và/hoặc video dài, thumbnail (chương trình FFmpeg)",
        "done": "Video mở được, đủ âm thanh, đúng khung hình",
    },
    "qa": {
        "inputs": "Prompt + lời + báo cáo nghiên cứu; sau dựng: file video thật",
        "outputs": "Kết luận đạt/cần sửa, danh sách vấn đề cụ thể",
        "done": "Không tên nghệ sĩ, không chép lời, khớp nghiên cứu; video qua kiểm tra kỹ thuật",
    },
    EXEC_ROLE: {
        "inputs": "Dòng thời gian báo cáo của yêu cầu, số liệu văn phòng",
        "outputs": "Báo cáo tổng kết ngắn cho giám đốc",
        "done": "Chỉ dựa trên dữ liệu thật trong ROC",
    },
}

INSTRUCTIONS = {
    "lead": "Bạn là trưởng nhóm của văn phòng làm nhạc AI (Suno) và video nhạc cho YouTube. Đọc yêu cầu của giám "
            "đốc, lập kế hoạch ngắn gọn và phân công: Lan (Nghiên cứu) phân tích bài mẫu, Hùng (Biên tập) viết "
            "prompt Suno và nội dung đăng, Mai (Kiểm tra) soát prompt và video, Tuấn (Sản xuất) dựng video. "
            "Nêu rõ rủi ro bản quyền: chỉ học phong cách, không chép giai điệu, lời hay tên nghệ sĩ.",
    "research": "Bạn là nghiên cứu viên âm nhạc. Bạn nhận SỐ ĐO THẬT do chương trình đo từ bài mẫu (BPM, tông, "
                "độ dài, độ lớn, đường năng lượng theo đoạn). Diễn giải thành mô tả phong cách dùng được để viết "
                "prompt: nhịp độ, tông/điệu thức, nhạc cụ khả dĩ, cấu trúc, cảm xúc, chất âm. Nói rõ điều gì là số "
                "đo, điều gì là suy luận. Không nêu tên nghệ sĩ hay bài hát trong phần gợi ý.",
    "writer": "Bạn là biên tập viên nhạc AI. Viết prompt phong cách cho Suno (tối đa 1000 ký tự, tiếng Anh, mô tả "
              "thể loại, nhịp độ BPM, nhạc cụ, giọng, cảm xúc, chất âm; KHÔNG tên nghệ sĩ/bài hát/thương hiệu). "
              "Nếu có lời: viết lời MỚI hoàn toàn, có nhãn [Verse], [Chorus], [Bridge]...; không chép hay phỏng "
              "theo lời có sẵn. Viết thêm gợi ý ảnh nền và tiêu đề/mô tả/thẻ YouTube.",
    "production": "Bạn là kỹ thuật viên sản xuất. Phần dựng video do chương trình FFmpeg thực hiện.",
    "qa": "Bạn là biên tập viên kiểm tra. Soát prompt Suno và lời: không có tên nghệ sĩ, tên bài hát hay thương "
          "hiệu có thật; lời không chép/phỏng theo bài có sẵn; khớp báo cáo nghiên cứu và yêu cầu; prompt rõ, "
          "dùng được. Chỉ ra đúng bài nào cần sửa và vì sao.",
    EXEC_ROLE: "Bạn là trợ lý ban điều hành. Viết báo cáo ngắn gọn bằng tiếng Việt từ dữ liệu được cung cấp, nêu "
               "rõ việc cần giám đốc quyết định.",
}

# --- schema đầu ra ------------------------------------------------------------

S_LEAD = {
    "type": "object", "required": ["summary", "assignments", "risks"],
    "properties": {
        "summary": {"type": "string", "minLength": 10},
        "assignments": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["who", "task"],
            "properties": {"who": {"type": "string"}, "task": {"type": "string", "minLength": 3}}}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}
S_RESEARCH = {
    "type": "object", "required": ["summary", "tempo", "key_mode", "instruments", "mood", "structure", "recommendations"],
    "properties": {
        "summary": {"type": "string", "minLength": 20},
        "tempo": {"type": "string"},
        "key_mode": {"type": "string"},
        "instruments": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "array", "items": {"type": "string"}},
        "production_notes": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    },
}
STYLE_MAX = 1000
S_WRITE = {
    "type": "object", "required": ["tracks", "image_prompts", "youtube"],
    "properties": {
        "tracks": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
            "type": "object", "required": ["title", "style", "lyrics"],
            "properties": {
                "title": {"type": "string", "minLength": 2, "maxLength": 100},
                "style": {"type": "string", "minLength": 20, "maxLength": STYLE_MAX},
                "lyrics": {"type": "string", "maxLength": 5000},
                "notes": {"type": "string"},
            }}},
        "image_prompts": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string", "minLength": 10}},
        "youtube": {"type": "object", "required": ["title", "description", "tags"], "properties": {
            "title": {"type": "string", "minLength": 5, "maxLength": 100},
            "description": {"type": "string", "minLength": 10, "maxLength": 4500},
            "tags": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
        }},
        "tracklist_template": {"type": "string"},
    },
}
S_OUTLINE = {
    "type": "object", "required": ["tracks", "image_prompts", "youtube"],
    "properties": {
        "tracks": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
            "type": "object", "required": ["title", "concept"],
            "properties": {
                "title": {"type": "string", "minLength": 2, "maxLength": 100},
                "concept": {"type": "string", "minLength": 5, "maxLength": 400},
            }}},
        "image_prompts": S_WRITE["properties"]["image_prompts"],
        "youtube": S_WRITE["properties"]["youtube"],
        "tracklist_template": {"type": "string"},
    },
}
S_TRACK = {
    "type": "object", "required": ["title", "style", "lyrics"],
    "properties": S_WRITE["properties"]["tracks"]["items"]["properties"],
}
S_CHECK = {
    "type": "object", "required": ["verdict", "summary", "issues"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "object", "required": ["reason"], "properties": {
            "track": {"type": ["integer", "null"]},
            "reason": {"type": "string", "minLength": 5}}}},
    },
}
S_SUMMARY = {
    "type": "object", "required": ["headline", "body"],
    "properties": {"headline": {"type": "string", "minLength": 5}, "body": {"type": "string", "minLength": 10}},
}
S_REPORT = {
    "type": "object", "required": ["headline", "sections", "decisions_needed"],
    "properties": {
        "headline": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object", "required": ["title", "body"],
                                                 "properties": {"title": {"type": "string"}, "body": {"type": "string"}}}},
        "decisions_needed": {"type": "array", "items": {"type": "string"}},
    },
}


# Minh lập kế hoạch kèm dàn bài
S_LEAD = {**S_LEAD, "required": S_LEAD["required"] + ["tracks", "image_prompts", "youtube"],
          "properties": {**S_LEAD["properties"], **S_OUTLINE["properties"]}}
S_TRACK_BRIEF = {
    "type": "object", "required": ["direction"],
    "properties": {
        "direction": {"type": "string", "minLength": 20, "maxLength": 1500},
        "tempo": {"type": "string"}, "key": {"type": "string"},
        "instruments": {"type": "array", "items": {"type": "string"}},
    },
}
