"""MOCK Claude Code — chỉ dùng trong kiểm thử."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fake_cli import main  # noqa: E402

main("claude")
