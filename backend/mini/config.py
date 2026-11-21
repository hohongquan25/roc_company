"""Cấu hình chạy. Đọc từ biến môi trường MINI_* hoặc file config.json trong thư mục dữ liệu.

Không có bí mật nào trong cấu hình này.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    data_dir: Path = ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    frontend_dist: Path = ROOT / "frontend" / "dist"
    # Giới hạn tiến trình CLI đồng thời (toàn công ty và theo từng CLI)
    max_concurrent_cli: int = 3
    max_concurrent_per_cli: dict = field(default_factory=lambda: {"claude": 2, "antigravity": 2})
    cli_timeout_sec: int = 900
    max_attempts: int = 3
    lease_sec: int = 120
    max_revisions: int = 2
    # Thời gian chờ mặc định khi hết lượt mà CLI không báo giờ reset
    quota_wait_sec: int = 1800
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    font_regular: str = r"C:\Windows\Fonts\segoeui.ttf"
    font_bold: str = r"C:\Windows\Fonts\segoeuib.ttf"
    # Video nhạc: khung hình, âm lượng mục tiêu (YouTube chuẩn hóa về khoảng -14 LUFS)
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30
    target_lufs: float = -14.0
    # Bài mẫu: chỉ tải để đo, xóa ngay sau khi phân tích; giới hạn độ dài để tránh tải nhầm video rất dài
    reference_max_seconds: int = 900
    # Đường dẫn tùy chọn tới file thực thi CLI (để trống = tìm trong PATH)
    cli_paths: dict = field(default_factory=dict)
    # URL Web App của Apps Script (xem docs/google-sheet.md). Để trống = không đẩy lên Google Sheet.
    sheets_webhook_url: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mini.db"

    @property
    def teams_dir(self) -> Path:
        return self.data_dir / "teams"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def public_dict(self) -> dict:
        d = asdict(self)
        return {k: str(v) if isinstance(v, Path) else v for k, v in d.items()}


def load_settings() -> Settings:
    s = Settings()
    if os.environ.get("MINI_DATA_DIR"):
        s.data_dir = Path(os.environ["MINI_DATA_DIR"])
    cfg = s.data_dir / "config.json"
    if cfg.exists():
        for k, v in json.loads(cfg.read_text(encoding="utf-8")).items():
            if hasattr(s, k):
                cur = getattr(s, k)
                setattr(s, k, Path(v) if isinstance(cur, Path) else v)
    if os.environ.get("MINI_PORT"):
        s.port = int(os.environ["MINI_PORT"])
    for d in (s.data_dir, s.teams_dir, s.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(s: Settings) -> None:
    """Dùng trong kiểm thử để trỏ sang thư mục dữ liệu tạm."""
    global _settings
    _settings = s
