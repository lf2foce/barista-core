"""ĐÀO TẠO NỘI BỘ — gói năng lực "training" (quyết định 0061): ngành nào cũng dùng (sale học chính sách dự án, nhân viên mới học quy trình…).

* Khoá học dựng từ tài liệu ĐANG HIỆU LỰC của kho (kb) — bài học trỏ tài liệu, không chép nội dung (tài liệu đổi bản thì bài học trỏ bản mới).
* Câu hỏi trắc nghiệm: người soạn hoặc trợ lý soạn từ tài liệu; MỖI câu kèm câu trích NGUYÊN VĂN kiểm bằng ``kb.quote`` (không có ⇒ bỏ câu). Câu do
  trợ lý soạn ở trạng thái NHÁP — người quản lý duyệt từng câu mới vào đề.
* Giao bài theo đội / người, có hạn. Trả lời từng câu (trên thẻ hoặc qua trợ lý); trả lời đủ ⇒ server chấm; ĐÁP ÁN không ra khỏi server trước khi
  nộp. Trượt ⇒ làm lại (câu trả lời cũ xoá, lần nộp giữ ở attempts).
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Desk,
    Hints,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Prep,
    Screen,
    Workspace,
    ai,
    field,
    ui,
)

# Gói 'training' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("training", "Đào tạo nội bộ",
     "Khoá học dựng từ tài liệu đang hiệu lực, câu hỏi trích nguyên văn (trợ lý soạn, người duyệt), giao bài theo đội, chấm điểm — ngành nào cũng dùng",
     grants=(("admin", "training", "all", "read write"), ("admin", "training.manage", "all", "read write"),
             ("director", "training", "all", "read write"), ("director", "training.manage", "all", "read write"),
             ("office", "training", "own", "read write"), ("staff", "training", "own", "read write"),),
     hints={"sale": Hints(suggest=(("training.my", "Kiểm tra giúp tôi bài đào tạo đang có: hỏi từng câu"),)),
            "team_lead": Hints(suggest=(("training.progress", "Ai trong đội chưa đạt bài đào tạo?"),))},
     tools={"sale": ("training.my",), "team_lead": ("training.progress",), "office": ("training.my",), "staff": ("training.my",)},   # 0218
     kind="capability",
     rules="""- ĐÀO TẠO: hỏi bài của tôi ⇒ training__my; người dùng muốn được KIỂM TRA bài ⇒ training__take lấy các câu chưa trả lời, hỏi TỪNG câu
  (đọc đủ lựa chọn A/B/C/D), người dùng chọn xong mới gọi training__answer (choice: A=0, B=1…); KHÔNG nói đáp án hay gợi ý trước khi họ chọn.
  Chấm điểm là việc của server — đọc kết quả training__answer trả về; câu sai giải thích bằng câu trích nguyên văn có trên thẻ.
""")

m = Module("training", "Đào tạo", pack="training", resources={"training": "Đào tạo — học & làm bài (của mình / đội / cả công ty)",
                                                              "training.manage": "Đào tạo — soạn khoá học, giao bài"}, setup=('training.manage',))

STATUS = {"assigned": "Chưa đạt", "passed": "Đã đạt", "failed": "Trượt — làm lại"}
TONES = {"assigned": "orange", "passed": "green", "failed": "red"}
COURSE_STATUS = {"draft": "Nháp", "published": "Đang dùng", "archived": "Ngừng"}
LETTERS = "ABCDEF"
MIN_QUESTIONS = 3
# Model soạn câu hỏi — test gắn FunctionModel vào đây; None ⇒ model cấu hình chung.
QUIZ_MODEL: Any = None


async def _course(ctx: Ctx, code: str) -> dict[str, Any]:
    c = await ctx.one("SELECT * FROM training.courses WHERE code = %s", (code.strip().upper(),))
    if c is None:
        raise NotFound(f"Không có khoá học {code}")
    return c


async def _active_questions(ctx: Ctx, course_id: UUID) -> list[dict[str, Any]]:
    return await ctx.all("SELECT * FROM training.questions WHERE course_id = %s AND status = 'active' ORDER BY created_at, id", (course_id,))


# ── Soạn khoá học (quyền training.manage) ──

class CourseIn(Input):
    code: str = field("Mã khoá", max_length=30, example="CS-HSR")
    title: str = field("Tên khoá", max_length=200, example="Chính sách bán hàng 2026")
    description: str = field("Mô tả", "", kind="textarea", max_length=1000)
    pass_pct: int = field("Điểm đạt (%)", 80, ge=1, le=100)


@m.command("upsert_course", resource="training.manage", title="Thêm / sửa khoá học")
async def upsert_course(ctx: Ctx, inp: CourseIn) -> dict[str, Any]:
    code = inp.code.strip().upper().replace(" ", "-")
    cid = await ctx.val(
        """INSERT INTO training.courses (code, title, description, pass_pct, created_by) VALUES (%s, %s, %s, %s, core.me())
           ON CONFLICT (tenant_id, code) DO UPDATE SET title = EXCLUDED.title, description = EXCLUDED.description, pass_pct = EXCLUDED.pass_pct
           RETURNING id""", (code, inp.title, inp.description, inp.pass_pct))
    return {"course_id": cid, "code": code}


@m.query("course_options", resource="training.manage", title="Khoá học", creates="training.upsert_course")
async def course_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, code || ' — ' || title AS label FROM training.courses WHERE status <> 'archived'"
                                    " ORDER BY code"))


class LessonIn(Input):
    course_code: str = field("Khoá học", options_query="training.course_options")
    document_id: UUID = field("Tài liệu (đang hiệu lực)", options_query="kb.current_options")
    title: str = field("Tên bài học", "", max_length=200, help="Trống = tên tài liệu")
    note: str = field("Ghi chú cho người học", "", kind="textarea", max_length=1000)


@m.command("add_lesson", resource="training.manage", title="Thêm bài học từ tài liệu")
async def add_lesson(ctx: Ctx, inp: LessonIn) -> dict[str, Any]:
    """Bài học TRỎ tài liệu của kho (người soạn phải đọc được tài liệu; tài liệu phải đang hiệu lực)."""
    c = await _course(ctx, inp.course_code)
    doc = await ctx.call("kb.read", document_id=inp.document_id, page_from=1, page_to=1)
    seq = int(await ctx.val("SELECT coalesce(max(seq), 0) + 1 FROM training.lessons WHERE course_id = %s", (c["id"],)) or 1)
    lid = await ctx.val("INSERT INTO training.lessons (course_id, seq, title, document_id, note) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (c["id"], seq, inp.title.strip() or doc["title"], inp.document_id, inp.note))
    return {"lesson_id": lid, "summary": f"Thêm bài {seq}: {inp.title.strip() or doc['title']}"}


async def _verified(ctx: Ctx, document_id: UUID | None, quote: str) -> dict[str, Any] | None:
    """Câu trích có NGUYÊN VĂN trong tài liệu đang hiệu lực không (kb.quote — code kiểm, không tin model)."""
    if document_id is None or len(quote.strip()) < 8:
        return None
    try:
        return await ctx.call("kb.quote", document_id=document_id, text=quote.strip())
    except (NotFound, Invalid, Denied):
        return None


def _choices(text: str) -> list[str]:
    out = [c.strip() for c in text.replace("\n", ";").split(";") if c.strip()]
    if not 2 <= len(out) <= 6:
        raise Invalid("Cần 2–6 lựa chọn, cách nhau bằng dấu chấm phẩy")
    return out


class QuestionIn(Input):
    course_code: str = field("Khoá học", options_query="training.course_options")
    prompt: str = field("Câu hỏi", kind="textarea", max_length=500)
    choices: str = field("Các lựa chọn (cách nhau “;”)", max_length=1000, example="10%; 20%; 30%; 50%")
    answer: int = field("Đáp án đúng (số thứ tự, từ 1)", ge=1, le=6, example=2)
    explanation: str = field("Giải thích", "", kind="textarea", max_length=500)
    document_id: UUID | None = field("Tài liệu căn cứ", None, options_query="kb.current_options")
    quote: str = field("Câu trích nguyên văn", "", kind="textarea", max_length=600, help="Có tài liệu thì bắt buộc khớp nguyên văn")


@m.command("add_question", resource="training.manage", title="Thêm câu hỏi")
async def add_question(ctx: Ctx, inp: QuestionIn) -> dict[str, Any]:
    c = await _course(ctx, inp.course_code)
    choices = _choices(inp.choices)
    if inp.answer > len(choices):
        raise Invalid(f"Đáp án {inp.answer} không có trong {len(choices)} lựa chọn")
    cite = ""
    if inp.document_id is not None:
        v = await _verified(ctx, inp.document_id, inp.quote)
        if v is None:
            raise Invalid("Câu trích không có NGUYÊN VĂN trong tài liệu đang hiệu lực — chép đúng câu trong tài liệu")
        cite = v["cite"]
    qid = await ctx.val(
        """INSERT INTO training.questions (course_id, prompt, choices, answer, explanation, quote, document_id, cite, status, source, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', 'manual', core.me()) RETURNING id""",
        (c["id"], inp.prompt, choices, inp.answer - 1, inp.explanation, inp.quote.strip(), inp.document_id, cite))
    return {"question_id": qid}


class Drafted(BaseModel):
    prompt: str
    choices: list[str]
    answer: int          # số thứ tự từ 0
    explanation: str
    quote: str           # câu NGUYÊN VĂN trong tài liệu làm căn cứ


class DraftSet(BaseModel):
    questions: list[Drafted]


QUIZ_RULES = ("Bạn soạn câu hỏi trắc nghiệm kiểm tra nhân viên đã nắm tài liệu nội bộ. Mỗi câu: 4 lựa chọn, đúng MỘT đáp án, hỏi điều cụ thể "
              "người làm việc phải nhớ (số, điều kiện, hạn, ai làm gì). quote = MỘT câu chép NGUYÊN VĂN từ tài liệu chứng minh đáp án (không sửa "
              "chữ). Không hỏi điều tài liệu không nói. Tiếng Việt.")


class Generate(Input):
    course_code: str = field("Khoá học", options_query="training.course_options")
    count: int = field("Số câu muốn soạn", 5, ge=1, le=15)


class CourseCode(Input):
    course_code: str = field("Khoá học", options_query="training.course_options")


@m.query("quiz_material", resource="training.manage", title="Tài liệu làm căn cứ soạn câu hỏi", agent="deny")
async def quiz_material(ctx: Ctx, inp: CourseCode) -> dict[str, Any]:
    """Khoá học + chữ của các tài liệu bài học trỏ tới (bản đang hiệu lực, quyền người hỏi) — phần ĐỌC của ``generate_questions``."""
    c = await _course(ctx, inp.course_code)
    lessons = await ctx.all("SELECT title, document_id FROM training.lessons WHERE course_id = %s AND document_id IS NOT NULL ORDER BY seq",
                            (c["id"],))
    if not lessons:
        raise Invalid("Khoá học chưa có bài học nào trỏ tài liệu")
    docs = []
    for ls in lessons:
        doc = await ctx.call("kb.read", document_id=ls["document_id"])
        docs.append({"document_id": ls["document_id"], "title": doc["title"], "text": doc["text"]})
    return {"course_id": c["id"], "title": c["title"], "docs": docs}


async def _draft(prep: Prep, inp: Generate) -> dict[str, Any]:
    """Bước CHUẨN BỊ (ADR 0200, review 12/11 F10): đọc tài liệu (một lần đọc DB ngắn) rồi gọi model — NGOÀI transaction ghi, không giữ kết nối / khoá
    dòng trong lúc chờ model. Ghi câu nháp là việc của handler (transaction ngắn, kiểm lại nguyên văn)."""
    mat = await prep.query("training.quiz_material", course_code=inp.course_code)
    material = "\n\n".join(f"=== Tài liệu: {d['title']} ===\n{d['text'][:6000]}" for d in mat["docs"])
    drafted = await ai.run(prep, f"Soạn {inp.count} câu hỏi cho khoá “{mat['title']}”.\n\n{material}", instructions=QUIZ_RULES,
                           output_type=DraftSet, category="training:quiz", modules=["training", "kb"], model=QUIZ_MODEL)
    return {"course_id": str(mat["course_id"]), "documents": [str(d["document_id"]) for d in mat["docs"]],
            "questions": [q.model_dump() for q in drafted.questions]}


@m.command("generate_questions", resource="training.manage", title="Trợ lý soạn câu hỏi từ tài liệu", agent="deny", prepare=_draft)
async def generate_questions(ctx: Ctx, inp: Generate) -> dict[str, Any]:
    """Trợ lý đọc tài liệu của các bài học và soạn câu hỏi NHÁP; CODE kiểm mỗi câu trích có nguyên văn trong tài liệu (không ⇒ bỏ câu), lựa chọn
    hợp lệ. Câu nháp chưa vào đề — người quản lý duyệt từng câu (training.set_question). Đọc + gọi model ở bước chuẩn bị ``_draft`` (ngoài transaction DB);
    ở đây chỉ kiểm lại rồi ghi."""
    c = await _course(ctx, inp.course_code)
    got = ctx.prepared
    if got is None:   # chạy thử / gọi lồng / chạy sau duyệt: không có bước chuẩn bị ⇒ không gọi model khi đang giữ transaction
        if ctx.preview:
            return {"created": 0, "dropped": [], "summary": f"Đã soạn tối đa {inp.count} câu nháp cho khoá “{c['title']}” — duyệt từng câu ở khoá học"}
        raise Invalid("Soạn câu hỏi bằng trợ lý chỉ chạy trực tiếp (bấm từ màn khoá học), không chạy kèm lệnh khác")
    if str(c["id"]) != got["course_id"]:
        raise Conflict("Khoá học vừa đổi trong lúc soạn — soạn lại")
    docs = [UUID(d) for d in got["documents"]]
    made, dropped = 0, []
    for q in (Drafted.model_validate(x) for x in got["questions"][: inp.count]):
        if not (2 <= len(q.choices) <= 6 and 0 <= q.answer < len(q.choices)):
            dropped.append(f"{q.prompt[:60]} — lựa chọn / đáp án không hợp lệ")
            continue
        hit = None
        for d in docs:   # kiểm NGUYÊN VĂN lúc ghi (tài liệu có thể vừa đổi bản trong lúc model soạn)
            if hit := await _verified(ctx, d, q.quote):
                break
        if hit is None:
            dropped.append(f"{q.prompt[:60]} — câu trích không có nguyên văn trong tài liệu")
            continue
        await ctx.exec(
            """INSERT INTO training.questions (course_id, prompt, choices, answer, explanation, quote, document_id, cite, status, source, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', 'ai', core.me())""",
            (c["id"], q.prompt.strip(), [x.strip() for x in q.choices], q.answer, q.explanation.strip(), q.quote.strip(), hit["document_id"],
             hit["cite"]))
        made += 1
    return {"created": made, "dropped": dropped,
            "summary": f"Soạn {made} câu nháp" + (f", bỏ {len(dropped)} câu không kiểm được nguyên văn" if dropped else "") + " — duyệt từng câu ở khoá học"}


class SetQuestion(Input):
    question_id: UUID = field("Câu hỏi", hidden=True)
    status: str = field("Trạng thái", options={"active": "Dùng trong đề", "retired": "Bỏ"})


@m.command("set_question", resource="training.manage", title="Duyệt / bỏ câu hỏi")
async def set_question(ctx: Ctx, inp: SetQuestion) -> dict[str, Any]:
    if inp.status not in ("active", "retired"):
        raise Invalid("Trạng thái không hợp lệ")
    n = await ctx.exec("UPDATE training.questions SET status = %s WHERE id = %s", (inp.status, inp.question_id))
    if not n:
        raise NotFound("Không thấy câu hỏi")
    return {"question_id": inp.question_id, "summary": "Đã duyệt vào đề" if inp.status == "active" else "Đã bỏ câu hỏi"}


class CourseRef(Input):
    code: str = field("Khoá học", options_query="training.course_options")


@m.command("publish_course", resource="training.manage", title="Đưa khoá học vào dùng")
async def publish_course(ctx: Ctx, inp: CourseRef) -> dict[str, Any]:
    c = await _course(ctx, inp.code)
    n = len(await _active_questions(ctx, c["id"]))
    if n < MIN_QUESTIONS:
        raise Invalid(f"Cần ít nhất {MIN_QUESTIONS} câu hỏi đã duyệt (đang có {n})")
    await ctx.exec("UPDATE training.courses SET status = 'published' WHERE id = %s", (c["id"],))
    return {"code": c["code"], "summary": f"Khoá “{c['title']}” đã dùng được — {n} câu hỏi"}


class Assign(Input):
    course_code: str = field("Khoá học", options_query="training.course_options")
    team_code: str = field("Giao cho đội", "", options_query="admin.team_options", help="Mọi thành viên đang làm của đội")
    emails: str = field("Hoặc giao cho người (email, cách nhau “,”)", "", max_length=1000)
    due_on: Day | None = field("Hạn hoàn thành", None, example="15/11/2026")


@m.command("assign", resource="training.manage", title="Giao bài đào tạo")
async def assign(ctx: Ctx, inp: Assign) -> dict[str, Any]:
    """Giao cho cả đội và / hoặc từng người. Người phạm vi đội chỉ giao trong đội mình. Đã đạt thì giữ nguyên; chưa đạt thì cập nhật hạn."""
    c = await _course(ctx, inp.course_code)
    if c["status"] != "published":
        raise Invalid("Khoá học chưa đưa vào dùng (publish_course)")
    users: dict[UUID, UUID | None] = {}
    if inp.team_code.strip():
        team = await ctx.val("SELECT id FROM core.teams WHERE code = %s", (inp.team_code.strip(),))
        if team is None:
            raise NotFound(f"Không có đội {inp.team_code}")
        if ctx.p.scope("training.manage") != "all" and team not in ctx.p.managed_teams:
            raise Denied("Chỉ giao được cho đội mình phụ trách")
        for r in await ctx.all("""SELECT tm.user_id, tm.team_id FROM core.team_members tm JOIN core.memberships ms
                                  ON ms.user_id = tm.user_id AND ms.tenant_id = core.tenant() AND ms.active WHERE tm.team_id = %s""", (team,)):
            users[r["user_id"]] = r["team_id"]
    for e in (x.strip().lower() for x in inp.emails.split(",") if x.strip()):
        uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships ms ON ms.user_id = u.id AND ms.tenant_id = core.tenant()
                               AND ms.active WHERE u.email = %s""", (e,))
        if uid is None:
            raise NotFound(f"Không có thành viên {e}")
        home = await ctx.home_team(uid)
        if ctx.p.scope("training.manage") != "all" and home not in ctx.p.managed_teams:
            raise Denied(f"{e} không thuộc đội bạn phụ trách")
        users[uid] = home
    if not users:
        raise Invalid("Chọn đội hoặc nhập email người học")
    for uid, team in users.items():
        await ctx.exec("""INSERT INTO training.assignments (course_id, user_id, team_id, due_on, assigned_by) VALUES (%s, %s, %s, %s, core.me())
                          ON CONFLICT (tenant_id, course_id, user_id) DO UPDATE SET due_on = EXCLUDED.due_on
                          WHERE training.assignments.status <> 'passed'""", (c["id"], uid, team, inp.due_on))
    due = f", hạn {inp.due_on:%d/%m/%Y}" if inp.due_on else ""
    await ctx.notify(list(users), kind="training", title=f"Bài đào tạo mới: {c['title']}", body=f"Học tài liệu và làm bài kiểm tra{due}.",
                     push=f"Bài đào tạo: {c['title']}", open={"query": "training.my", "input": {}})
    return {"assigned": len(users), "summary": f"Giao “{c['title']}” cho {len(users)} người{due}"}


# ── Học & làm bài (quyền training: của mình) ──

async def _my_assignment(ctx: Ctx, assignment_id: UUID) -> dict[str, Any]:
    a = await ctx.one("""SELECT a.*, c.title, c.pass_pct, c.status AS course_status, c.id AS cid FROM training.assignments a
                         JOIN training.courses c ON c.id = a.course_id WHERE a.id = %s""", (assignment_id,))
    if a is None:
        raise NotFound("Không thấy bài được giao")
    return a


class AssignmentRef(Input):
    assignment_id: UUID = field("Bài", hidden=True)


class Answer(Input):
    assignment_id: UUID = field("Bài", hidden=True)
    question_id: UUID = field("Câu hỏi", hidden=True)
    choice: int = field("Lựa chọn (từ 0)", ge=0, le=5, hidden=True)


@m.command("answer", resource="training", title="Trả lời câu hỏi", agent="allow")
async def answer(ctx: Ctx, inp: Answer) -> dict[str, Any]:
    """Trả lời MỘT câu của bài CỦA MÌNH. Trả lời đủ mọi câu ⇒ server chấm ngay: đạt ⇒ xong; trượt ⇒ lưu lần nộp, xoá câu trả lời để làm lại.
    Trợ lý làm thay được (người học nói đáp án trong chat) — đây là bài của chính người đó."""
    a = await _my_assignment(ctx, inp.assignment_id)
    if a["user_id"] != ctx.p.user_id:
        raise Denied("Chỉ người được giao mới làm bài này")
    if a["status"] == "passed":
        raise Invalid("Bài đã đạt")
    qs = await _active_questions(ctx, a["cid"])
    q = next((x for x in qs if x["id"] == inp.question_id), None)
    if q is None:
        raise NotFound("Câu hỏi không thuộc đề hiện tại")
    if inp.choice >= len(q["choices"]):
        raise Invalid("Lựa chọn không có")
    await ctx.exec("""INSERT INTO training.answers (assignment_id, question_id, choice) VALUES (%s, %s, %s)
                      ON CONFLICT (assignment_id, question_id) DO UPDATE SET choice = EXCLUDED.choice, answered_at = now()""",
                   (a["id"], q["id"], inp.choice))
    got = {r["question_id"]: r["choice"] for r in await ctx.all("SELECT question_id, choice FROM training.answers WHERE assignment_id = %s",
                                                                (a["id"],))}
    left = [x for x in qs if x["id"] not in got]
    if left:
        return {"answered": len(qs) - len(left), "total": len(qs), "submitted": False,
                "summary": f"Đã ghi câu trả lời — còn {len(left)} câu"}
    wrong = [str(x["id"]) for x in qs if got[x["id"]] != x["answer"]]
    correct = len(qs) - len(wrong)
    pct = round(100 * correct / len(qs))
    passed = pct >= a["pass_pct"]
    await ctx.exec("""INSERT INTO training.attempts (assignment_id, correct, total, score_pct, passed, wrong)
                      VALUES (%s, %s, %s, %s, %s, %s)""", (a["id"], correct, len(qs), pct, passed, Jsonb(wrong)))
    await ctx.exec("""UPDATE training.assignments SET attempts = attempts + 1, best_pct = greatest(coalesce(best_pct, 0), %s),
                      status = %s, passed_at = CASE WHEN %s THEN now() END WHERE id = %s""",
                   (pct, "passed" if passed else "failed", passed, a["id"]))
    if not passed:
        await ctx.exec("DELETE FROM training.answers WHERE assignment_id = %s", (a["id"],))
    return {"answered": len(qs), "total": len(qs), "submitted": True, "score_pct": pct, "passed": passed,
            "summary": f"{'Đạt' if passed else 'Chưa đạt'}: {correct}/{len(qs)} câu đúng ({pct}%, cần {a['pass_pct']}%)"
                       + ("" if passed else " — xem câu sai rồi làm lại")}


@m.query("my", resource="training", title="Bài đào tạo của tôi")
async def my(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT a.id, c.title, a.status, a.due_on, a.best_pct, a.attempts, c.pass_pct,
                                   (a.status <> 'passed' AND a.due_on < core.today()) AS late
                            FROM training.assignments a JOIN training.courses c ON c.id = a.course_id
                            WHERE a.user_id = core.me() ORDER BY (a.status = 'passed'), a.due_on NULLS LAST""")
    for r in rows:
        r["late"] = "Quá hạn" if r["late"] else ""
    todo = [r for r in rows if r["status"] != "passed"]
    return ui.table(rows, [
        ui.col("late", "", "badge", tones={"Quá hạn": "red"}), ui.col("title", "Khoá học", width=280),
        ui.col("status", "Trạng thái", "badge", labels=STATUS, tones=TONES), ui.col("due_on", "Hạn", "date"),
        ui.col("best_pct", "Điểm cao nhất (%)", "number"), ui.col("pass_pct", "Cần đạt (%)", "number"), ui.col("attempts", "Lần làm", "number"),
    ], title="Bài đào tạo của tôi", kpis=[ui.kpi("Còn phải làm", len(todo), tone="orange" if todo else "green")],
        empty="Chưa có bài đào tạo nào được giao")


@m.query("take", resource="training", title="Học & làm bài")
async def take(ctx: Ctx, inp: AssignmentRef) -> dict[str, Any]:
    """Tài liệu cần học + câu chưa trả lời (KHÔNG có đáp án). Đã nộp ⇒ kết quả lần gần nhất, câu sai kèm giải thích + câu trích nguyên văn."""
    a = await _my_assignment(ctx, inp.assignment_id)
    mine = a["user_id"] == ctx.p.user_id
    lessons = await ctx.all("SELECT seq, title, document_id, note FROM training.lessons WHERE course_id = %s ORDER BY seq", (a["cid"],))
    blocks: list[dict[str, Any]] = []
    last = await ctx.one("SELECT * FROM training.attempts WHERE assignment_id = %s ORDER BY submitted_at DESC LIMIT 1", (a["id"],))
    if a["status"] == "passed":
        blocks.append(ui.b_callout("ok", f"Đã đạt — {a['best_pct']}%", f"Cần {a['pass_pct']}%. Làm {a['attempts']} lần."))
    elif last:
        blocks.append(ui.b_callout("warn", f"Lần trước: {last['score_pct']}% — chưa đạt (cần {a['pass_pct']}%)",
                                   "Đọc lại các câu sai bên dưới rồi làm lại."))
    blocks.append(ui.b_list([{"title": f"Bài {ls['seq']}: {ls['title']}", "subtitle": ls["note"] or "",
                              **({"open": {"query": "kb.document", "input": {"document_id": str(ls["document_id"])}}} if ls["document_id"] else {})}
                             for ls in lessons] or [{"title": "Khoá học chưa có bài học"}], "Tài liệu cần học"))
    qs = await _active_questions(ctx, a["cid"])
    if last and (wrong := set(last["wrong"])):
        blocks.append(ui.b_list([{"title": q["prompt"], "tone": "bad",
                                  "subtitle": f"Đáp án: {LETTERS[q['answer']]}. {q['choices'][q['answer']]}"
                                              + (f" — {q['explanation']}" if q["explanation"] else "")
                                              + (f" · “{q['quote']}” ({q['cite']})" if q["quote"] else "")}
                                 for q in qs if str(q["id"]) in wrong], "Câu sai lần trước"))
    if a["status"] != "passed" and a["course_status"] == "published":
        got = {r["question_id"] for r in await ctx.all("SELECT question_id FROM training.answers WHERE assignment_id = %s", (a["id"],))}
        left = [q for q in qs if q["id"] not in got]
        blocks.append(ui.b_kpis([ui.kpi("Đã trả lời", f"{len(qs) - len(left)}/{len(qs)}", "text")]))
        for i, q in enumerate(left[:20]):
            acts = [ui.act("training.answer", f"{LETTERS[k]}. {ch}", {"assignment_id": str(a["id"]), "question_id": str(q["id"]), "choice": k},
                           "instant")
                    for k, ch in enumerate(q["choices"])] if mine else []
            blocks.append(ui.b_callout("muted", f"Câu {len(qs) - len(left) + i + 1}: {q['prompt']}", "", actions=acts))
    return ui.card(a["title"], blocks, subtitle=f"{STATUS[a['status']]}" + (f" · hạn {a['due_on']:%d/%m/%Y}" if a["due_on"] else ""),
                   icon="graduationcap", ask={"title": "Nhờ trợ lý kiểm tra bài", "prompt": f"Kiểm tra bài “{a['title']}” cho tôi: hỏi từng câu"})


# ── Quản lý ──

@m.query("courses", resource="training.manage", title="Khoá học")
async def courses(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT c.id, c.code, c.title, c.status, c.pass_pct,
                  (SELECT count(*) FROM training.lessons l WHERE l.course_id = c.id) AS lessons,
                  (SELECT count(*) FROM training.questions q WHERE q.course_id = c.id AND q.status = 'active') AS questions,
                  (SELECT count(*) FROM training.questions q WHERE q.course_id = c.id AND q.status = 'draft') AS drafts,
                  (SELECT count(*) FROM training.assignments a WHERE a.course_id = c.id) AS assigned,
                  (SELECT count(*) FROM training.assignments a WHERE a.course_id = c.id AND a.status = 'passed') AS passed
           FROM training.courses c ORDER BY c.status, c.code""")
    return ui.table(rows, [
        ui.col("code", "Mã"), ui.col("title", "Khoá học", width=260), ui.col("status", "Trạng thái", "badge", labels=COURSE_STATUS,
                                                                             tones={"draft": "orange", "published": "green", "archived": "gray"}),
        ui.col("lessons", "Bài học", "number"), ui.col("questions", "Câu trong đề", "number"), ui.col("drafts", "Câu nháp chờ duyệt", "number"),
        ui.col("assigned", "Đã giao", "number"), ui.col("passed", "Đã đạt", "number"), ui.col("pass_pct", "Điểm đạt (%)", "number"),
    ], title="Khoá học", empty="Chưa có khoá học — thêm khoá, thêm bài học từ tài liệu, rồi nhờ trợ lý soạn câu hỏi")


@m.query("course", resource="training.manage", title="Soạn khoá học")
async def course(ctx: Ctx, inp: CourseRef) -> dict[str, Any]:
    c = await _course(ctx, inp.code)
    lessons = await ctx.all("SELECT seq, title, document_id FROM training.lessons WHERE course_id = %s ORDER BY seq", (c["id"],))
    qs = await ctx.all("SELECT * FROM training.questions WHERE course_id = %s AND status <> 'retired' ORDER BY status, created_at", (c["id"],))
    blocks: list[dict[str, Any]] = [ui.b_list([{"title": f"Bài {ls['seq']}: {ls['title']}",
                                                **({"open": {"query": "kb.document", "input": {"document_id": str(ls["document_id"])}}}
                                                   if ls["document_id"] else {})} for ls in lessons] or [{"title": "Chưa có bài học"}], "Bài học")]
    for q in qs:
        body = " · ".join(f"{LETTERS[k]}. {ch}" + (" ✓" if k == q["answer"] else "") for k, ch in enumerate(q["choices"]))
        facts = [x for x in (q["explanation"], f"“{q['quote']}” — {q['cite']}" if q["quote"] else "") if x]
        acts = ([ui.act("training.set_question", "Duyệt vào đề", {"question_id": str(q["id"]), "status": "active"}, "primary")]
                if q["status"] == "draft" else []) + [ui.act("training.set_question", "Bỏ", {"question_id": str(q["id"]), "status": "retired"},
                                                             "destructive")]
        blocks.append(ui.b_callout("muted" if q["status"] == "active" else "warn",
                                   ("" if q["status"] == "active" else "[Nháp — trợ lý soạn] ") + q["prompt"], body, facts=facts, actions=acts))
    actions = [ui.act("training.add_lesson", "Thêm bài học", {"course_code": c["code"]}),
               ui.act("training.generate_questions", "Trợ lý soạn câu hỏi", {"course_code": c["code"]}),
               ui.act("training.add_question", "Thêm câu hỏi", {"course_code": c["code"]})]
    if c["status"] == "draft":
        actions.append(ui.act("training.publish_course", "Đưa vào dùng", {"code": c["code"]}, "primary"))
    else:
        actions.append(ui.act("training.assign", "Giao bài", {"course_code": c["code"]}, "primary"))
    return ui.card(c["title"], blocks, subtitle=f"{c['code']} · {COURSE_STATUS[c['status']]} · đạt từ {c['pass_pct']}%", icon="graduationcap",
                   actions=actions)


@m.query("progress", resource="training", title="Tiến độ đào tạo", min_scope="team")
async def progress(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Bài đã giao trong phạm vi người xem (trưởng nhóm: đội mình; quản lý: cả công ty) — ai chưa đạt, ai quá hạn."""
    rows = await ctx.all("""SELECT a.id, u.name AS person, c.title, a.status, a.due_on, a.best_pct, a.attempts,
                                   (a.status <> 'passed' AND a.due_on < core.today()) AS late
                            FROM training.assignments a JOIN training.courses c ON c.id = a.course_id JOIN core.users u ON u.id = a.user_id
                            ORDER BY (a.status = 'passed'), a.due_on NULLS LAST, u.name LIMIT 2000""")
    for r in rows:
        r["late"] = "Quá hạn" if r["late"] else ""
    late = sum(1 for r in rows if r["late"])
    done = sum(1 for r in rows if r["status"] == "passed")
    return ui.table(rows, [
        ui.col("late", "", "badge", tones={"Quá hạn": "red"}), ui.col("person", "Người học"), ui.col("title", "Khoá học", width=240),
        ui.col("status", "Trạng thái", "badge", labels=STATUS, tones=TONES), ui.col("due_on", "Hạn", "date"),
        ui.col("best_pct", "Điểm cao nhất (%)", "number"), ui.col("attempts", "Lần làm", "number"),
    ], title="Tiến độ đào tạo", kpis=[ui.kpi("Đã đạt", f"{done}/{len(rows)}", "text"), ui.kpi("Quá hạn", late, tone="red" if late else None)],
        limit=2000, empty="Chưa giao bài nào")


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("training", "Đào tạo", "graduationcap.fill", (
    Screen("my_training", "Bài đào tạo của tôi", "graduationcap", "training.my", row_click="open", open=Open("training.take", {"assignment_id": "id"}, "Học & làm bài"),
           desk=Desk("do", rank=150)),
    Screen("training_progress", "Tiến độ đào tạo", "chart.bar.xaxis", "training.progress", min_scope="team"),
    Screen("courses", "Khoá học", "books.vertical", "training.courses", actions=(A("training.upsert_course", style="primary"),),
           row_actions=(A("training.add_lesson", "Thêm bài học", bind={"course_code": "code"}),
                        A("training.generate_questions", "Trợ lý soạn câu hỏi", bind={"course_code": "code"}),
                        A("training.publish_course", "Đưa vào dùng", bind={"code": "code"}, when={"status": ["draft"]}, style="primary"),
                        A("training.assign", "Giao bài", bind={"course_code": "code"}, when={"status": ["published"]}, style="primary")),
           open=Open("training.course", {"code": "code"}, "Soạn khoá học")),
), order=40))
