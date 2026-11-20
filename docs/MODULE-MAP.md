# Bản đồ module

> Sinh từ code — KHÔNG sửa tay. Sinh lại: `cd backend && uv run python scripts/module_map.py`; `tests/test_module_map.py` đỏ khi file này lệch code.
> Vì sao chia như vậy: [OVERVIEW.md](OVERVIEW.md), ADR 0045, 0056, 0088.

18 gói · 30 module · 1019 thao tác (280 nội bộ) · 20 bên nghe sự kiện.

## Gói

| Gói | Tên | Loại | Bắt buộc | Module |
|---|---|---|---|---|
| `base` | Nền | lõi | — | `admin`, `apps`, `asks`, `connect`, `connectors`, `core`, `flow`, `health`, `kb`, `memory`, `papers`, `work` |
| `books` | Sổ sách & nghĩa vụ | năng lực | — | `books` |
| `compose` | Soạn với AI (thử nghiệm) | năng lực | — | `compose` |
| `crm` | Khách hàng | năng lực | `directory`, `schedule` | `crm`, `leadflow` |
| `directory` | Cá nhân & tổ chức | năng lực | — | `parties` |
| `finance` | Tài chính | năng lực | `directory` | `acct` |
| `findings` | Báo cáo chạy song song | năng lực | — | `findings` |
| `hr` | Hồ sơ làm việc | năng lực | `directory` | `hr` |
| `kpi` | KPI & xếp hạng | năng lực | — | `kpi` |
| `marketing` | Marketing | năng lực | `crm` | `mkt` |
| `meet` | Gọi video | năng lực | — | `meet` |
| `mgmt` | Báo cáo quản trị | năng lực | — | `mgmt` |
| `pay` | Lương & thù lao | năng lực | `directory`, `finance`, `hr` | `pay` |
| `schedule` | Lịch & nguồn lực | năng lực (luôn bật) | — | `schedule` |
| `talk` | Trao đổi | năng lực | — | `talk` |
| `timekeeping` | Chấm công | năng lực | `hr` | `timekeeping` |
| `training` | Đào tạo nội bộ | năng lực | — | `training` |
| `word` | Văn bản Word | năng lực | — | `word` |

## Module

| Module | Tên | Gói | Schema | Mã | Lệnh | Truy vấn | Nội bộ |
|---|---|---|---|---|---|---|---|
| `acct` | Kế toán | `finance` | `acct` | `modules/acct/` (13 file) | 68 | 83 | 55 |
| `admin` | Quản trị | `base` | — | `modules/admin.py` | 38 | 43 | 12 |
| `apps` | App của tôi | `base` | `apps` | `modules/apps.py` | 11 | 20 | 2 |
| `asks` | Yêu cầu gửi bên ngoài | `base` | `asks` | `modules/asks.py` | 10 | 3 | 4 |
| `books` | Sổ sách & nghĩa vụ | `books` | `books` | `modules/books.py` | 9 | 14 | 6 |
| `compose` | Soạn với AI | `compose` | `compose` | `modules/compose/` (7 file) | 20 | 6 | 1 |
| `connect` | Ứng dụng kết nối | `base` | — | `modules/connect.py` | 9 | 3 | 4 |
| `connectors` | Kết nối ngoài | `base` | `connectors` | `modules/connectors.py` | 10 | 6 | 6 |
| `core` | Lõi | `base` | `core` | `kernel/ai_hints.py`, `kernel/ai_skills.py`, `kernel/ai_tiers.py`, `kernel/ai_tools.py`, `kernel/core_ops.py`, `kernel/docread_policy.py`, `kernel/embed_policy.py`, `kernel/history.py`, `kernel/idempotency_log.py`, `kernel/import_log.py` | 32 | 28 | 9 |
| `crm` | Khách hàng | `crm` | `crm` | `modules/crm/` (18 file) | 24 | 49 | 42 |
| `findings` | Báo cáo chạy song song | `findings` | — | `modules/findings.py` | 0 | 7 | 1 |
| `flow` | Quy trình & việc | `base` | `flow` | `modules/flow/` (15 file) | 37 | 49 | 33 |
| `health` | Sức khoẻ dữ liệu | `base` | — | `modules/health.py` | 0 | 1 | 0 |
| `hr` | Hồ sơ làm việc | `hr` | `hr` | `modules/hr/` (2 file) | 13 | 14 | 4 |
| `kb` | Tài liệu | `base` | `kb` | `modules/kb/` (4 file) | 15 | 23 | 12 |
| `kpi` | KPI & xếp hạng | `kpi` | `kpi` | `modules/kpi.py` | 4 | 6 | 2 |
| `leadflow` | Chia lead & hạn xử lý | `crm` | `leadflow` | `modules/leadflow/` (7 file) | 16 | 20 | 13 |
| `meet` | Gọi video | `meet` | `meet` | `modules/meet.py` | 17 | 11 | 4 |
| `memory` | Trí nhớ | `base` | `memory` | `modules/memory.py` | 4 | 4 | 1 |
| `mgmt` | Báo cáo quản trị | `mgmt` | `mgmt` | `modules/mgmt.py` | 2 | 3 | 0 |
| `mkt` | Marketing | `marketing` | `mkt` | `modules/mkt/` (3 file) | 15 | 32 | 16 |
| `papers` | Giấy tờ | `base` | `papers` | `modules/papers/` (3 file) | 12 | 15 | 3 |
| `parties` | Cá nhân & tổ chức | `directory` | `parties` | `modules/parties.py` | 20 | 16 | 19 |
| `pay` | Lương & thù lao | `pay` | `pay` | `modules/pay/` (7 file) | 15 | 17 | 7 |
| `schedule` | Lịch & nguồn lực | `schedule` | `schedule` | `modules/schedule/` (5 file) | 18 | 16 | 6 |
| `talk` | Trao đổi | `talk` | `talk` | `modules/talk.py` | 16 | 18 | 7 |
| `timekeeping` | Chấm công | `timekeeping` | `timekeeping` | `modules/timekeeping/` (8 file) | 22 | 16 | 8 |
| `training` | Đào tạo | `training` | `training` | `modules/training.py` | 8 | 7 | 0 |
| `word` | Văn bản Word | `word` | `word` | `modules/word/` (2 file) | 8 | 5 | 0 |
| `work` | Việc theo dõi | `base` | `work` | `modules/work.py` | 6 | 5 | 3 |

## Từng module

### `acct` — Kế toán

- **Mã:** `acct/__init__.py`, `acct/assurance.py`, `acct/bank.py`, `acct/corrections.py`, `acct/dossier.py`, `acct/dossier_logic.py`, `acct/expenses.py`, `acct/goods.py`, `acct/ledger_read.py`, `acct/periods.py`, `acct/placements.py`, `acct/settlements.py`, `acct/vat.py`
- **Tài nguyên quyền:** `acct`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `acct.advances_left` ← `pay`
  - `acct.confidential_open` ← `acct`
  - `acct.health_report` ← `health`
  - `acct.party_open_vnd` ← `parties`
  - `acct.payable_states` ← `pay`
  - `acct.period_open` ← `pay`
  - `acct.receivable_due` ← `flow`
  - `acct.recognize_payable` ← `pay`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `acct.balances_for` → `remittance`
  - `acct.category` → `ledger_ref`
  - `acct.collections` → `company_figures`
  - `acct.cost_object` → `ledger_ref`
  - `acct.issue_receivable` → `receivable`
  - `acct.ledger_balances` → `ledger_read`
  - `acct.ledger_lines` → `ledger_read`
  - `acct.ledger_month_totals` → `company_figures`
  - `acct.my_settlements` → `settlement`
  - `acct.owner_positions` → `settlement`
  - `acct.payable_states` → `settlement`
  - `acct.payout_payables` → `settlement`
  - `acct.payout_states` → `settlement`
  - `acct.payouts_open` → `settlement`
  - `acct.period_open` → `ledger_ref`
  - `acct.place` → `placement`
  - `acct.place_balances` → `placement`
  - `acct.place_refund` → `placement`
  - `acct.place_reverse` → `placement`
  - `acct.pnl_account` → `ledger_ref`
  - `acct.position` → `settlement`
  - `acct.post_entry` → `journal`
  - `acct.prepare_payout` → `settlement`
  - `acct.receipt_facts` → `receivable`
  - `acct.receivables_for` → `receivable`
  - `acct.recognize_payable` → `settlement`
  - `acct.recognize_recovery` → `settlement`
  - `acct.release_receipts` → `receivable`
  - `acct.release_recovery` → `settlement`
  - `acct.request_remittance` → `remittance`
  - `acct.source_settlement_blockers` → `settlement`
  - `acct.void_payables` → `settlement`
  - `acct.void_receivables` → `receivable`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `acct.cost_offering` ← —
  - `acct.cost_offerings` ← —
  - `acct.payout_flags` ← —
  - `acct.receipt_change_guard` ← —
- **Sự kiện phát:** `acct.dossier_closed`, `acct.dossier_opened`, `acct.invoice_recorded`, `acct.payment_received`, `acct.payout_transferred`, `acct.receipt_reversed`, `acct.receivable_overdue`, `acct.remittance_changed`
- **Nghe sự kiện:** `acct.receivable_overdue` → `acct.open_collection`, `acct.payment_received` → `acct.recheck_on_payment`, `acct.payment_received` → `acct.wake_on_payment`
- **Cò tự động hoá:** `acct.dossier_closed`, `acct.dossier_opened`, `acct.payment_received`, `acct.receivable_overdue`
- **Tìm nhanh:** `acct.search_receivables`
- **Việc theo dõi:** `collection`, `discrepancy`
- **Vòng quét:** `acct.cash_digest`, `acct.cash_low_alert`, `acct.check_money`, `acct.follow_overdue`, `acct.link_receivable_parties`, `acct.scan_discrepancies`

### `admin` — Quản trị

- **Tài nguyên quyền:** —
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `admin.delivery_health` ← `health`
  - `admin.members_with_grant` ← `acct`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `admin.members_with_grant` → `follow_up`
- **Sự kiện phát:** `admin.member_deactivated`, `admin.team_members_changed`, `admin.team_saved`
- **Nghe sự kiện:** `admin.member_deactivated` → `admin.revoke_credentials`, `core.system_error` → `admin.tell_admins_about_errors`
- **Vòng quét:** `admin.review_agent_policies`

### `apps` — App của tôi

- **Tài nguyên quyền:** —
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `apps.set_published` ← `mgmt`
  - `apps.shared` ← `mgmt`

### `asks` — Yêu cầu gửi bên ngoài

- **Tài nguyên quyền:** `asks`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `asks.notify_owner` ← `asks`, `flow`
  - `asks.open` ← `flow`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `asks.close_subject` → `outreach`
  - `asks.notify_owner` → `outreach`
  - `asks.open` → `outreach`
- **Sự kiện phát:** `asks.item_answered`
- **Vòng quét:** `asks.remind`

### `books` — Sổ sách & nghĩa vụ

- **Tài nguyên quyền:** `books`, `books.setup`
- **Sự kiện phát:** `subject.changed`, `subject.created`
- **Đối tượng:** `books.company`, `books.period`
- **Bộ kiểm:** `books.filings_done`
- **Vòng quét:** `books.open_due`, `books.sync_rules`

### `compose` — Soạn với AI

- **Mã:** `compose/__init__.py`, `compose/check.py`, `compose/editorial.py`, `compose/html.py`, `compose/layout.py`, `compose/render.py`, `compose/vn.py`
- **Tài nguyên quyền:** —
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `compose.brochure` ← `findings.plug_brochure`

### `connect` — Ứng dụng kết nối

- **Tài nguyên quyền:** —

### `connectors` — Kết nối ngoài

- **Tài nguyên quyền:** `connectors`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `connectors.health_report` ← `health`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `connectors.of_kind` → `connector`
  - `connectors.save` → `connector`
- **Vòng quét:** `connectors.sync`

### `core` — Lõi

- **Tài nguyên quyền:** `core.admin`, `core.ai_policy`, `core.ai_skills`, `core.ai_tools`, `core.ai_usage`, `core.approvals`, `core.audit`, `core.operator`, `ledger`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `core.default_ai_tier` ← `admin`
  - `core.default_role_ai_tier` ← `admin`
  - `core.default_role_tools` ← `admin`
  - `core.install_skill` ← `admin`
- **Vòng quét:** `core.purge_idempotency`, `core.purge_import_rows`

### `crm` — Khách hàng

- **Mã:** `crm/__init__.py`, `crm/agenda.py`, `crm/calls.py`, `crm/deal_link.py`, `crm/findings.py`, `crm/identity.py`, `crm/idle.py`, `crm/imported.py`, `crm/journey.py`, `crm/leads.py`, `crm/lost_reasons.py`, `crm/morning.py`, `crm/my_month.py`, `crm/reports.py`, `crm/reveal.py`, `crm/routing.py`, `crm/screens.py`, `crm/stages.py`
- **Tài nguyên quyền:** `crm`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `crm.campaign_leads` ← `mkt`
  - `crm.contact_info` ← `leadflow`
  - `crm.departed_leads` ← `leadflow`
  - `crm.findings_report` ← `findings`
  - `crm.follow_up_due` ← `flow`
  - `crm.health_report` ← `health`
  - `crm.intake_lead` ← `leadflow`
  - `crm.lead_ad_of` ← `mkt`
  - `crm.lead_cards` ← `leadflow`
  - `crm.lead_match_keys` ← `mkt`
  - `crm.lead_span` ← `findings`, `mkt`
  - `crm.lead_stats` ← `mkt`
  - `crm.lead_stats_by_ad` ← `mkt`
  - `crm.offering_by_code` ← `leadflow`, `mkt`
  - `crm.outcome_info` ← `mkt`
  - `crm.place` ← `leadflow`
  - `crm.revive_lost` ← `leadflow`
  - `crm.workload` ← `leadflow`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `crm.customer_party` → `identity`
  - `crm.funnel` → `company_figures`
  - `crm.leads_for_phones` → `call_log`
  - `crm.link_deal` → `lead_conversion`
  - `crm.offering_by_code` → `call_log`
  - `crm.record_call` → `call_log`
  - `crm.record_lead_outcome` → `lead_conversion`
  - `crm.reopen_lead` → `lead_conversion`
  - `crm.source_of_leads` → `company_figures`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `crm.brief_extra` ← —
  - `crm.campaign` ← `mkt.plug_campaign`
  - `crm.customer_due` ← —
  - `crm.lead_outcome` ← —
  - `crm.lead_story` ← —
  - `crm.offering` ← —
  - `crm.offering_options` ← —
- **Cắm vào:** `schedule.calendar_items`, `schedule.markers`, `schedule.quick_add`
- **Sự kiện phát:** `crm.lead_assigned`, `crm.lead_created`, `crm.lead_outcome_recorded`, `crm.lead_stage_changed`, `subject.changed`
- **Nghe sự kiện:** `parties.party_renamed` → `crm.follow_party_name`, `schedule.booking_changed` → `crm.on_booking_changed`
- **Cò tự động hoá:** `crm.lead_assigned`, `crm.lead_created`, `crm.lead_stage_changed`
- **Chỉ số:** `crm.metric_appointments`, `crm.metric_calls`, `crm.metric_connected`, `crm.metric_leads`
- **Tìm nhanh:** `crm.search_customers`
- **Đối tượng:** `crm.lead`
- **Vòng quét:** `crm.heal_appointments`, `crm.remind_appointments`

### `findings` — Báo cáo chạy song song

- **Tài nguyên quyền:** `findings`
- **Cắm vào:** `compose.brochure`

### `flow` — Quy trình & việc

- **Mã:** `flow/__init__.py`, `flow/assign.py`, `flow/automations.py`, `flow/cases.py`, `flow/due_phrase.py`, `flow/evidence.py`, `flow/messages.py`, `flow/plan_import.py`, `flow/plan_logic.py`, `flow/plans.py`, `flow/requests.py`, `flow/schedule_rules.py`, `flow/screens.py`, `flow/tasks.py`, `flow/timeline.py`
- **Tài nguyên quyền:** `flow`, `flow.automations`, `flow.templates`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `flow.calendar_of` ← `schedule`
  - `flow.cancel_plan_tasks` ← `flow`
  - `flow.check_task` ← `flow`
  - `flow.close_plan_requests` ← `flow`
  - `flow.notify_plan` ← `flow`
  - `flow.open_for_subject` ← `flow`
  - `flow.plan_requests` ← `flow`
  - `flow.plan_state` ← `flow`
  - `flow.plan_task_graph` ← `flow`
  - `flow.plan_tasks` ← `flow`
  - `flow.remind_plan_tasks` ← `flow`
  - `flow.sync` ← `flow`
  - `flow.task_due` ← `flow`
  - `flow.workload` ← `flow`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `flow.module_task` → `follow_up`
  - `flow.subject_status` → `follow_up`
- **Gọi họ thao tác bằng quyền hệ thống:** `check`, `metric`, `subject`
- **Cắm vào:** `schedule.markers`, `talk.message_actions`
- **Sự kiện phát:** `flow.plan_closed`, `flow.plan_created`, `flow.plan_reopened`, `flow.task_cancelled`, `flow.task_done`, `flow.task_reopened`
- **Nghe sự kiện:** `asks.item_answered` → `flow.brief_answered`, `subject.changed` → `flow.on_subject_changed`, `subject.created` → `flow.on_subject_created`, `(mọi cò)` → `flow.run_automations`, `flow.automation_due` → `flow.run_dated_automation`, `subject.changed` → `flow.wake_checked_tasks`
- **Cò tự động hoá:** `flow.plan_closed`
- **Tìm nhanh:** `flow.search_plans`, `flow.search_tasks`
- **Đối tượng:** `flow.case`, `flow.plan`, `flow.request`, `flow.task`
- **Việc theo dõi:** `case`
- **Vòng quét:** `flow.check_goals`, `flow.check_tasks`, `flow.escalate_requests`, `flow.escalate_stuck`, `flow.remind_due`, `flow.sweep_dated_automations`

### `health` — Sức khoẻ dữ liệu

- **Tài nguyên quyền:** `health`

### `hr` — Hồ sơ làm việc

- **Mã:** `hr/__init__.py`, `hr/credentials.py`
- **Tài nguyên quyền:** `hr`, `hr.credential_private`, `hr.credentials`, `hr.terms`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `hr.terms_on` ← `pay`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `hr.structure_on` → `structure`
- **Nghe sự kiện:** `admin.team_members_changed` → `hr.credentials_follow_team`
- **Vòng quét:** `hr.remind_credentials`

### `kb` — Tài liệu

- **Mã:** `kb/__init__.py`, `kb/intake.py`, `kb/readers.py`, `kb/vectors.py`
- **Tài nguyên quyền:** `kb.common`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `kb.receive` ← `asks`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `kb.extracted` → `doc_read`
  - `kb.file_digest` → `doc_read`
  - `kb.mentions` → `doc_read`
- **Cắm vào:** `talk.thread_files`
- **Sự kiện phát:** `kb.document_added`
- **Nghe sự kiện:** `admin.team_saved` → `kb.team_shelf`
- **Tìm nhanh:** `kb.search_documents`
- **Vòng quét:** `kb.embed_sweep`

### `kpi` — KPI & xếp hạng

- **Tài nguyên quyền:** `kpi`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `kpi.peer_targets` ← `kpi`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `kpi.company_targets` → `company_figures`
- **Gọi họ thao tác bằng quyền hệ thống:** `metric`

### `leadflow` — Chia lead & hạn xử lý

- **Mã:** `leadflow/__init__.py`, `leadflow/findings.py`, `leadflow/history.py`, `leadflow/reads.py`, `leadflow/routing.py`, `leadflow/screens.py`, `leadflow/sweep.py`
- **Tài nguyên quyền:** `leadflow`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `leadflow.contact_gate` ← `crm`
  - `leadflow.findings_report` ← `findings`
  - `leadflow.follow_owner` ← `crm`
  - `leadflow.give` ← `leadflow`
  - `leadflow.health_report` ← `health`
  - `leadflow.lead_sla` ← `crm`
  - `leadflow.move` ← `leadflow`
  - `leadflow.receiver_check` ← `crm`
  - `leadflow.returned` ← `leadflow`
  - `leadflow.route` ← `leadflow`
  - `leadflow.score_rows` ← `leadflow`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `leadflow.external_lead_days` → `company_figures`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `leadflow.away` ← `timekeeping.plug_away`
  - `leadflow.contact_proof` ← —
- **Nghe sự kiện:** `admin.member_deactivated` → `leadflow.holder_left`, `admin.team_members_changed` → `leadflow.team_changed`
- **Vòng quét:** `leadflow.enforce_sla`

### `meet` — Gọi video

- **Tài nguyên quyền:** `meet`, `meet.settings`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `meet.peer_state` ← `meet`
- **Cắm vào:** `talk.thread_actions`
- **Sự kiện phát:** `meet.attendance_confirmed`, `meet.call_ended`
- **Vòng quét:** `meet.end_stale`, `meet.purge_signals`

### `memory` — Trí nhớ

- **Tài nguyên quyền:** `memory.legacy`

### `mgmt` — Báo cáo quản trị

- **Tài nguyên quyền:** `mgmt`

### `mkt` — Marketing

- **Mã:** `mkt/__init__.py`, `mkt/ad_control.py`, `mkt/findings.py`
- **Tài nguyên quyền:** `mkt`, `mkt.ad_control`, `mkt.ad_policy`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `mkt.campaign_cpl` ← `crm`
  - `mkt.campaign_owners` ← `leadflow`
  - `mkt.cashflow_lines` ← `acct`
  - `mkt.cost_share_terms` ← `leadflow`
  - `mkt.findings_report` ← `findings`
  - `mkt.health_report` ← `health`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `mkt.ad_results` → `company_figures`
  - `mkt.fx_rates` → `company_figures`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `mkt.ad_apply` ← —
  - `mkt.ad_convert` ← —
  - `mkt.ad_objects` ← —
- **Cắm vào:** `crm.campaign`
- **Sự kiện phát:** `mkt.ad_changed`
- **Nghe sự kiện:** `crm.lead_outcome_recorded` → `mkt.queue_ad_conversion`
- **Tìm nhanh:** `mkt.search_campaigns`
- **Vòng quét:** `mkt.dispatch_ad_changes`, `mkt.send_ad_conversions`, `mkt.watch_ads`

### `papers` — Giấy tờ

- **Mã:** `papers/__init__.py`, `papers/compose.py`, `papers/template.py`
- **Tài nguyên quyền:** `papers`, `papers.templates`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `papers.company` → `paper_draft`
  - `papers.save_draft` → `paper_draft`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `papers.field_catalog` ← —
  - `papers.field_values` ← —
  - `papers.subject_options` ← —
  - `papers.subjects_seen` ← —
- **Tìm nhanh:** `papers.search_papers`

### `parties` — Cá nhân & tổ chức

- **Tài nguyên quyền:** `parties`, `parties.contacts`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `parties.attach_contact` ← `crm`
  - `parties.call_consent` ← `meet`
  - `parties.cluster` ← `crm`
  - `parties.contact_card` ← `crm`
  - `parties.ensure_counterparty` ← `acct`
  - `parties.ensure_member` ← `hr`, `pay`
  - `parties.ensure_person` ← `crm`
  - `parties.ensure_supplier` ← `acct`
  - `parties.logins` ← `hr`
  - `parties.names` ← `acct`, `hr`, `pay`
  - `parties.record_contact_consent` ← `crm`
  - `parties.resolve_for_payment` ← `acct`, `hr`, `pay`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `parties.add_dependant_for` → `identity`
  - `parties.call_consent` → `consent`
  - `parties.consent_summaries` → `consent`
  - `parties.consent_summary` → `consent`
  - `parties.ensure_counterparty` → `identity`
  - `parties.ensure_member` → `identity`
  - `parties.ensure_person` → `identity`
  - `parties.names` → `identity`
  - `parties.record_contact_consent` → `consent`
  - `parties.referrers_on` → `structure`
  - `parties.resolve_for_payment` → `identity`
- **Sự kiện phát:** `parties.guardian_ended`, `parties.party_renamed`
- **Tìm nhanh:** `parties.search_parties`
- **Vòng quét:** `parties.end_guardians_of_age`

### `pay` — Lương & thù lao

- **Mã:** `pay/__init__.py`, `pay/cashflow.py`, `pay/engine.py`, `pay/policy.py`, `pay/runs.py`, `pay/screens.py`, `pay/work.py`
- **Tài nguyên quyền:** `pay`, `pay.policy`, `pay.slips`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `pay.cashflow_lines` ← `acct`
  - `pay.record_work` ← `timekeeping`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `pay.record_work` → `work_log`

### `schedule` — Lịch & nguồn lực

- **Mã:** `schedule/__init__.py`, `schedule/calendar.py`, `schedule/imported.py`, `schedule/rrule.py`, `schedule/view.py`
- **Tài nguyên quyền:** `schedule`, `schedule.details`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `schedule.book` ← `crm`, `meet`, `timekeeping`
  - `schedule.finish` ← `crm`
  - `schedule.release` ← `crm`
  - `schedule.times_of` ← `crm`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `schedule.book` → `booking`
  - `schedule.release` → `booking`
  - `schedule.resources_for` → `booking`
- **Gọi họ thao tác bằng quyền hệ thống:** `plug`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `schedule.calendar_items` ← `crm.plug_calendar_items`
  - `schedule.markers` ← `crm.plug_markers`, `flow.plug_markers`
  - `schedule.quick_add` ← `crm.plug_quick_add`
- **Sự kiện phát:** `schedule.booking_changed`
- **Vòng quét:** `schedule.extend_series`

### `talk` — Trao đổi

- **Tài nguyên quyền:** `talk`, `talk.audit`
- **Điểm cắm của module này** (điểm ← thao tác cắm vào):
  - `talk.message_actions` ← `flow.plug_message_actions`
  - `talk.thread_actions` ← `meet.plug_thread_actions`
  - `talk.thread_files` ← `kb.plug_thread_files`
- **Nghe sự kiện:** `admin.team_members_changed` → `talk.team_room_on_members`, `admin.team_saved` → `talk.team_room_on_save`
- **Tìm nhanh:** `talk.search_files`, `talk.search_messages`
- **Đối tượng:** `talk.direct`, `talk.room`

### `timekeeping` — Chấm công

- **Mã:** `timekeeping/__init__.py`, `timekeeping/closing.py`, `timekeeping/common.py`, `timekeeping/leave.py`, `timekeeping/policy.py`, `timekeeping/punches.py`, `timekeeping/screens.py`, `timekeeping/sheet.py`
- **Tài nguyên quyền:** `timekeeping`, `timekeeping.policy`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `timekeeping.work_hours` ← `schedule`
- **Cắm vào:** `leadflow.away`

### `training` — Đào tạo

- **Tài nguyên quyền:** `training`, `training.manage`

### `word` — Văn bản Word

- **Mã:** `word/__init__.py`, `word/docx_model.py`
- **Tài nguyên quyền:** —

### `work` — Việc theo dõi

- **Tài nguyên quyền:** `work`
- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):
  - `work.handoff` ← `acct`, `flow`
  - `work.open` ← `acct`, `flow`
  - `work.wake` ← `acct`, `flow`
- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):
  - `work.open` → `follow_up`
  - `work.wake` → `follow_up`
