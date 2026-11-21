"""Tín hiệu điều khiển luồng mà handler ném ra; worker chuyển thành trạng thái công việc."""
from __future__ import annotations


class JobSignal(Exception):
    pass


class Retryable(JobSignal):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class Fatal(JobSignal):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class WaitQuota(JobSignal):
    def __init__(self, until: float, message: str):
        super().__init__(message)
        self.until = until


class WaitConnection(JobSignal):
    def __init__(self, message: str, recheck_at: float | None = None):
        super().__init__(message)
        self.recheck_at = recheck_at


class WaitApproval(JobSignal):
    def __init__(self, approval_id: int, message: str):
        super().__init__(message)
        self.approval_id = approval_id


class NeedsReconcile(JobSignal):
    pass


class Reschedule(JobSignal):
    """Chưa đến lúc (vd: video còn đang xử lý) — chạy lại sau, không tính là lần thử lỗi."""

    def __init__(self, delay: float, message: str):
        super().__init__(message)
        self.delay = delay


class Cancelled(JobSignal):
    pass
