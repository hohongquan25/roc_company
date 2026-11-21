"""Hồi quy cho các lỗi phát hiện khi chạy nghiệm thu thật."""
from PIL import Image, ImageDraw

from mini import db
from mini.media import render
from mini.orchestrator import worker


def test_scheduler_never_creates_work_by_itself(env):
    """Nhân viên chỉ làm theo yêu cầu của bạn: lập lịch định kỳ không tự tạo việc."""
    env.team()
    db.set_setting("company_running", True)
    worker.schedule_tick()
    assert db.q1("SELECT COUNT(*) n FROM jobs")["n"] == 0


def test_math_symbols_use_fallback_font(env):
    f = render._font(True, 60)
    parts = render.runs("Cường độ ∝ 1/λ⁴", f)
    assert any("∝" in s and fnt is not f for s, fnt in parts)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert render.tlen(d, "a ∝ b", f) > 0


def test_timestamps_format():
    assert render.fmt_ts(0) == "00:00" and render.fmt_ts(65.4) == "01:05" and render.fmt_ts(3725) == "1:02:05"
