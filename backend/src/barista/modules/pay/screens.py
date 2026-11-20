"""Màn của gói Lương (app chỉ vẽ): kỳ lương → phiếu; phiếu của tôi (điện thoại); sổ công; mức; khoản; loại hợp đồng; quy tắc luật; bất thường."""

from typing import Any

from barista.api import Action as A
from barista.api import Desk, Line, Open, Screen, Workspace, kpi, line
from barista.modules.pay import m


def _say_anomalies(o: Any) -> list[Line | None]:
    n = int(kpi(o, "Cần xử lý"))
    rows = o.get("rows") or [] if isinstance(o, dict) else []
    held = sum(1 for r in rows if r.get("what") == "held" and "chưa xác minh" in str(r.get("detail")))
    return [line("bad", f"{held} phiếu lương dừng vì quy tắc luật chưa xác minh", "Chờ kế toán xác minh từng quy tắc (Lương › Quy tắc luật); kỳ chạy bóng "
                 "có số để so", ask="Vì sao phiếu lương đang dừng?") if held else None,
            line("warn", f"{n} việc lương cần xử lý", "Thiếu mức, thiếu loại hợp đồng, phiếu dừng, quy tắc chờ xác minh") if n else None]


m.workspace(Workspace("pay", "Lương & thù lao", "banknote", (
    # màn ĐỐI TƯỢNG của chính người xem (không phải hộp việc): mỗi người thấy phiếu đã chốt của mình, mở ra là phiếu (dùng được trên điện thoại)
    Screen("pay_my", "Phiếu lương", "doc.text", "pay.my_slips", tint="green",
           row_click="open", open=Open("pay.slip", {"slip_id": "id"}, "Phiếu lương")),
    Screen("pay_runs", "Kỳ lương", "calendar", "pay.runs", tint="green", min_scope="all",
           actions=(A("pay.draft_run", style="primary"),),
           row_actions=(A("pay.compute_run", "Tính lại", bind={"run_id": "id"}, when={"state": ["draft", "shadow"]}),
                        A("pay.submit_run", "Gửi duyệt", bind={"run_id": "id"}, when={"state": ["draft"]}, style="primary"),
                        A("pay.return_to_draft", "Rút về nháp", bind={"run_id": "id"}, when={"state": ["submitted"]}),
                        A("pay.discard_run", "Xoá kỳ nháp", bind={"run_id": "id"}, when={"state": ["draft", "shadow"]}, style="destructive")),
           row_click="open", open=Open("pay.run", {"run_id": "id"}, "Kỳ lương")),
    Screen("pay_entries", "Sổ công", "list.bullet.rectangle", "pay.entries", tint="orange", min_scope="all",
           actions=(A("pay.import_work", style="primary"), A("pay.request_entry"))),
    Screen("pay_anomalies", "Bất thường lương", "exclamationmark.triangle", "pay.anomalies", tint="red", min_scope="all",
           desk=Desk("do", say=_say_anomalies, rank=40)),
    Screen("pay_rates", "Mức trả", "tag", "pay.rates", tint="teal", min_scope="all", actions=(A("pay.set_rate", style="primary"),)),
    Screen("pay_components", "Khoản lương", "list.number", "pay.components", tint="indigo", min_scope="all",
           actions=(A("pay.upsert_component", style="primary"),),
           row_actions=(A("pay.upsert_component", "Sửa", prefill={"code": "code", "label": "label", "kind": "kind", "calculator": "calculator",
                                                                  "taxable": "taxable", "expense_account": "expense_account", "source": "source",
                                                                  "active": "active"}),)),
    Screen("pay_terms", "Điều kiện trả", "person.text.rectangle", "hr.terms", tint="blue", min_scope="all", actions=(A("hr.set_terms", style="primary"),)),
    Screen("pay_dependants", "Người phụ thuộc", "person.2", "hr.dependants", tint="blue", min_scope="all",
           actions=(A("hr.add_dependant", style="primary"),),
           row_actions=(A("hr.end_dependant", "Thôi đăng ký", bind={"dependant_id": "id"}, when={"open": ["yes"]}),)),
    Screen("pay_rules", "Quy tắc luật", "building.columns", "pay.rules", min_scope="all"),
), order=46))
