"""Màn hình (0069): phân hệ Kinh doanh do crm sở hữu + màn nhập liệu góp vào Quản trị."""

from barista.api import Action as A
from barista.api import Desk, Open, Screen, Workspace
from barista.modules.crm import m
from barista.modules.crm.morning import _attn_brief, _attn_brief_2

# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
LEAD_ROW_ACTIONS = (
    A("crm.log_activity", bind={"lead_id": "id"}, style="primary"),
    A("crm.set_stage", bind={"lead_id": "id"}),
    A("crm.assign", bind={"lead_id": "id"}),
)
m.workspace(Workspace("sales", "Kinh doanh", "person.2.fill", (
    Screen("today", "Khách cần gọi hôm nay", "sun.max.fill", "crm.brief", tint="orange", actions=(A("leadflow.pause"),),
           desk=Desk("do", say=lambda o: [_attn_brief(o), _attn_brief_2(o)], rank=20)),
    Screen("agenda", "Lịch hẹn khách", "calendar.circle.fill", "crm.agenda", tint="red", desk=Desk("do", rank=25)),
    Screen("leads", "Khách & lead", "person.crop.rectangle.stack.fill", "crm.leads", tint="blue",
           # slot: gói ngành có thể thay nút này bằng form riêng của ngành (0124) — nhập Excel vẫn ở màn Nhập liệu (``leads_entry``) với ``create_lead``
           actions=(A("crm.create_lead", style="primary", slot="crm.add_lead"),), row_actions=LEAD_ROW_ACTIONS,
           row_click="open", open=Open("crm.journey", {"lead_id": "id"}, "Hành trình khách")),
), order=10))
m.screens("admin", (
    Screen("leads_entry", "Khách / lead", "person.crop.rectangle.stack.fill", "crm.leads", tint="blue", min_scope="all", needs_action=True, group="Nhập liệu",
           actions=(A("crm.create_lead", style="primary"), A("leadflow.intake")), row_actions=LEAD_ROW_ACTIONS),
), rank=60)
