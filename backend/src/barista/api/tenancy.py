"""Công ty KHÁCH do một công ty dịch vụ tạo (0063, 1.8): tạo / đồng bộ thành viên do hợp đồng cấp / bật AI / khoá người nghỉ ở mọi khách,
chạy lệnh hay truy vấn TRONG công ty khách bằng quyền hệ thống của công ty đó (``run_in`` / ``query_in``), đọc nhiều công ty khách bằng quyền người
xem (``gather``). Mọi hàm kiểm quan hệ: chỉ công ty có ``provisioned_by`` = ``provider`` (gói truyền ``ctx.p.tenant_id``)."""

from barista.kernel.tenancy import (
    active_members,
    deactivate_everywhere,
    ensure_company,
    gather,
    query_in,
    run_in,
    set_ai,
    sync_members,
)

__all__ = ["active_members", "deactivate_everywhere", "ensure_company", "gather", "query_in", "run_in", "set_ai", "sync_members"]
