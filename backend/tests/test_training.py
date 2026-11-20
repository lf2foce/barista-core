"""Đào tạo nội bộ (quyết định 0061): khoá học từ tài liệu đang hiệu lực, câu hỏi trích nguyên văn, giao bài theo đội, chấm ở server. Ranh giới:
đáp án không lộ trước khi nộp; không làm hộ bài người khác; trưởng nhóm chỉ giao trong đội; câu trích bịa bị chặn / bị bỏ; trợ lý soạn chỉ ra NHÁP."""

import json
import uuid
from typing import Any

from barista.kernel import executor
from barista.modules import training
from tests.conftest import Who, ok, owner_val, q


async def _assignment(p: Any) -> dict[str, Any]:
    rows = (await q(p, "training.my"))["rows"]
    return next(r for r in rows if r["title"].startswith("Chính sách bán hàng và chiết khấu"))


async def _questions(tid: Any) -> list[dict[str, Any]]:
    rows = await owner_val("""SELECT jsonb_agg(jsonb_build_object('id', q.id, 'answer', q.answer) ORDER BY q.created_at, q.id) FROM training.questions q
                              JOIN training.courses c ON c.id = q.course_id WHERE c.tenant_id = %s AND c.code = 'CS-BH' AND q.status = 'active'""",
                           (tid,))
    return rows


async def test_learner_sees_no_answers_then_fails_reviews_and_passes(who: Who, demo: dict[str, Any]) -> None:
    sale2 = await who("sale2")                                                     # đội KD-A: được giao khi nạp mẫu
    a = await _assignment(sale2)
    assert a["status"] == "assigned"
    page = await q(sale2, "training.take", assignment_id=a["id"])
    text = json.dumps(page, ensure_ascii=False)
    assert "Khách thanh toán đủ trong 7 ngày" in text and "✓" not in text and "chiết khấu 2% giá trị hợp đồng" not in text   # câu hỏi có, đáp án / câu trích chưa
    qs = await _questions(demo["tenant"])
    res: dict[str, Any] = {}
    for i, qq in enumerate(qs):                                                    # sai 2 / 4 ⇒ 50% < 75% ⇒ trượt
        choice = qq["answer"] if i >= 2 else (qq["answer"] + 1) % 4
        res = await ok(sale2, "training.answer", assignment_id=a["id"], question_id=qq["id"], choice=choice)
    assert res["submitted"] and not res["passed"] and res["score_pct"] == 50
    page = await q(sale2, "training.take", assignment_id=a["id"])
    wrong = next(b for b in page["blocks"] if b.get("title") == "Câu sai lần trước")
    assert len(wrong["items"]) == 2 and "Khách thanh toán đủ trong 7 ngày kể từ ngày ký hợp đồng" in json.dumps(wrong, ensure_ascii=False)   # sau khi nộp: căn cứ
    assert await owner_val("SELECT count(*) FROM training.answers WHERE assignment_id = %s", (a["id"],)) == 0             # làm lại từ đầu
    for qq in qs:
        res = await ok(sale2, "training.answer", assignment_id=a["id"], question_id=qq["id"], choice=qq["answer"])
    assert res["passed"] and res["score_pct"] == 100
    assert (await _assignment(sale2))["status"] == "passed"
    r = await executor.run(sale2, "training.answer", {"assignment_id": a["id"], "question_id": qs[0]["id"], "choice": 0})
    assert r.status == "invalid"                                                   # đã đạt thì thôi


async def test_nobody_answers_someone_elses_test_and_leads_assign_only_their_team(who: Who, demo: dict[str, Any]) -> None:
    sale1, sale3, truonga = await who("sale1"), await who("sale3"), await who("truonga")
    a = await _assignment(sale1)
    qs = await _questions(demo["tenant"])
    r = await executor.run(sale3, "training.answer", {"assignment_id": a["id"], "question_id": qs[0]["id"], "choice": 0})
    assert r.status in ("not_found", "denied")                                     # RLS: bài của người khác không thấy
    r = await executor.run(truonga, "training.answer", {"assignment_id": a["id"], "question_id": qs[0]["id"], "choice": 0})
    assert r.status == "denied"                                                    # trưởng nhóm thấy nhưng không làm hộ
    r = await executor.run(truonga, "training.assign", {"course_code": "CS-BH", "team_code": "KD-B"})
    assert r.status == "denied"
    prog = await q(truonga, "training.progress")
    people = {r["person"] for r in prog["rows"]}
    assert "Nguyễn Văn An" in people and "Đặng Gia Khánh" not in people           # tiến độ đội mình thôi
    assert (await executor.query(sale1, "training.progress", {})).status == "denied"
    proposed = await executor.run(sale1.as_agent(), "training.answer", {"assignment_id": a["id"], "question_id": qs[0]["id"], "choice": 1})
    assert proposed.status == "ok"                                                 # trợ lý ghi câu trả lời người học nói (bài của chính họ)


async def test_quotes_must_be_verbatim_and_ai_drafts_wait_for_review(who: Who) -> None:
    from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    gd = await who("giamdoc")
    code = f"T{uuid.uuid4().hex[:5].upper()}"
    await ok(gd, "training.upsert_course", code=code, title="Khoá thử")
    doc = await owner_val("SELECT id FROM kb.documents WHERE title = 'Chính sách bán hàng và chiết khấu 2026 (mẫu)' LIMIT 1")
    await ok(gd, "training.add_lesson", course_code=code, document_id=doc)
    r = await executor.run(gd, "training.add_question", {"course_code": code, "prompt": "Chiết khấu?", "choices": "1%; 9%", "answer": 2,
                                                         "document_id": str(doc), "quote": "Thanh toán sớm được chiết khấu 9% giá trị hợp đồng."})
    assert r.status == "invalid" and "NGUYÊN VĂN" in r.error

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"questions": [
            {"prompt": "Bảo hành bao lâu?", "choices": ["6 tháng", "12 tháng", "18 tháng", "24 tháng"], "answer": 1, "explanation": "",
             "quote": "Bảo hành 12 tháng kể từ ngày nghiệm thu; phản hồi yêu cầu hỗ trợ trong 4 giờ làm việc."},
            {"prompt": "Phí quản lý?", "choices": ["1%", "2%"], "answer": 0, "explanation": "", "quote": "Phí quản lý dịch vụ là 1% mỗi năm."},
        ]})])

    training.QUIZ_MODEL = FunctionModel(reply, model_name="quiz-fake")
    try:
        out = await ok(gd, "training.generate_questions", course_code=code, count=5)
    finally:
        training.QUIZ_MODEL = None
    assert out["created"] == 1 and len(out["dropped"]) == 1                        # câu trích bịa ⇒ bỏ
    r = await executor.run(gd, "training.publish_course", {"code": code})
    assert r.status == "invalid"                                                   # câu nháp chưa vào đề
    draft = await owner_val("SELECT q.id FROM training.questions q JOIN training.courses c ON c.id = q.course_id WHERE c.code = %s AND q.status = 'draft'",
                            (code,))
    await ok(gd, "training.set_question", question_id=draft, status="active")
    assert (await owner_val("SELECT status || '|' || cite FROM training.questions WHERE id = %s", (draft,))).startswith("active|Chính sách bán hàng")
    r = await executor.run(gd.as_agent(), "training.generate_questions", {"course_code": code})
    assert r.status == "denied"                                                    # trợ lý không tự kích soạn đề
