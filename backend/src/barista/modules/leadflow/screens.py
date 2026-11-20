"""Màn của chia lead (0069) + câu tín hiệu bàn việc (0094): khai trên màn (``desk=Desk(..., say=...)``); ``agent/desk.py`` chạy bằng quyền người xem."""

from typing import Any

from barista.api import Action as A
from barista.api import Desk, Line, Screen, kpi, line, num
from barista.modules.leadflow import m


def _attn_overview(o: Any) -> Line | None:
    return (line("bad", f"{num(kpi(o, 'Kẹt — không ai nhận'))} lead kẹt, chưa có người nhận",
            "Không ai đủ điều kiện nhận — giao tay (có gợi ý) hoặc chỉnh luật")
            if kpi(o, "Kẹt — không ai nhận") else None)


def _attn_queue(o: Any) -> Line | None:
    return (line("warn", f"{num(kpi(o, 'Chờ chia'))} lead đang chờ anh/chị chia",
            "Em đã gợi ý người nhận cho từng lead — bấm là giao", "Chia giúp tôi các lead đang chờ theo gợi ý")
            if kpi(o, "Chờ chia") else None)


def _attn_overview_2(o: Any) -> Line | None:
    return (line("warn", f"{num(kpi(o, 'Thu hồi (7 ngày)'))} lead bị thu hồi tuần này", "Có sale để quá hạn gọi — xem ai và vì sao",
                 "Tuần này ai để lead quá hạn nhiều nhất?") if kpi(o, "Thu hồi (7 ngày)") else None)


def _attn_grace(o: Any) -> Line | None:
    return (line("bad", f"{num(kpi(o, 'Quá hạn — trong ân hạn'))} lead quá hạn liên hệ, sắp bị thu hồi",
                 "Trưởng nhóm đã được báo — chuyển tay ngay nếu người giữ không gọi được", "Lead nào đang quá hạn gọi, nên chuyển cho ai?")
            if kpi(o, "Quá hạn — trong ân hạn") else None)


def _attn_returned(o: Any) -> Line | None:
    return (line("accent", f"{num(kpi(o, 'Khách quay lại (7 ngày)'))} khách cũ quay lại qua kênh khác tuần này",
                 "Người đang chăm đã được báo; lead đã mất được mở lại theo luật", "Khách nào quay lại tuần này, ai đang chăm?")
            if kpi(o, "Khách quay lại (7 ngày)") else None)


def _attn_mine(o: Any) -> list[Line | None]:
    return [line("bad", f"{num(kpi(o, 'Quá hạn liên hệ'))} lead của anh/chị đã quá hạn liên hệ", "Gọi ngay trước khi bị thu hồi")
            if kpi(o, "Quá hạn liên hệ") else None,
            line("warn", f"{num(kpi(o, 'Chờ bấm nhận'))} lead đang chờ anh/chị bấm nhận", "Không nhận kịp thì lead chuyển người khác")
            if kpi(o, "Chờ bấm nhận") else None,
            line("accent", f"{num(kpi(o, 'Khách quay lại (7 ngày)'))} khách của anh/chị vừa quay lại", "Gọi lại khi khách còn nóng")
            if kpi(o, "Khách quay lại (7 ngày)") else None]


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.screens("sales", (
    Screen("leadflow", "Chia lead & hạn gọi", "arrow.triangle.branch", "leadflow.overview", tint="blue",
           desk=Desk("decide", say=lambda o: [_attn_overview(o), _attn_grace(o), _attn_overview_2(o), _attn_returned(o)], menu=True, rank=40)),
    Screen("lead_queue", "Lead chờ chia", "tray.and.arrow.down.fill", "leadflow.queue", tint="indigo", min_scope="team",
           desk=Desk("decide", say=_attn_queue, team_only=True, rank=50)),
    Screen("lead_policy", "Lead đi đâu", "signpost.right", "leadflow.policy", needs_action=True, actions=(A("leadflow.set_rule", style="primary"),)),
    Screen("lead_mine", "Lead của tôi — hạn liên hệ", "timer", "leadflow.mine", tint="orange", max_scope="own", desk=Desk("do", say=_attn_mine, rank=35)),
    Screen("lead_scorecard", "Phản hồi lead theo người", "chart.bar.xaxis", "leadflow.scorecard", min_scope="team"),
    Screen("lead_members", "Thành viên [[offering|l]]", "person.3", "leadflow.members", needs_action=True,
           actions=(A("leadflow.set_member", style="primary"),)),
), rank=10)
