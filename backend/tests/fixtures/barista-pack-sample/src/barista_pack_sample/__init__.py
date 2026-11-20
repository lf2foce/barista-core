"""Gói mẫu ngoài lõi: một module, một bảng, một truy vấn — chỉ import ``barista.api``."""

from typing import Any

from barista.api import Ctx, Module, NoInput, Pack, Screen, Workspace, ui

Pack("sample", "Gói mẫu", "Gói cài từ bản phân phối riêng — thước đo nạp gói", kind="industry", requires=("crm",),
     grants=(("admin", "sample", "all", "read write"),))

m = Module("sample", "Mẫu", pack="sample", resources={"sample": "Dữ liệu mẫu"})


@m.query("notes", resource="sample", title="Ghi chú mẫu")
async def notes(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("SELECT id::text, body FROM sample.notes ORDER BY created_at")
    return ui.table(rows, [ui.col("body", "Ghi chú")], title="Ghi chú mẫu")


m.workspace(Workspace("sample", "Mẫu", "star", (Screen("sample_notes", "Ghi chú", "note.text", "sample.notes"),), order=90))
