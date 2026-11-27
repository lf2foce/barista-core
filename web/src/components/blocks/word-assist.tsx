"use client";

import { FileCheck, Search, TriangleAlert } from "lucide-react";
import type { AssistSource } from "@/lib/word";

// Phần dùng chung của trợ lý soạn thảo ở màn Soạn thảo (0227 §2.8, 0251): gom các đoạn gửi cho trợ lý, và dòng "dựa vào đâu" dưới câu trả lời.
// Khung trợ lý riêng trong trình sửa (kiểu cũ) đã bỏ 27/11 — chỉ còn MỘT giao diện soạn là màn Soạn thảo.

const MAX_WHOLE = 400;

type Para = { id: string; text: string };

/** Các đoạn sửa được (mã gốc, lần gặp đầu) — trong vùng chọn nếu có, không thì cả văn bản. */
export function collect(sheet: HTMLElement, range: Range | null): { paras: Para[]; scope: "selection" | "document" } {
  const all = [...sheet.querySelectorAll<HTMLElement>("[data-seg] > p[data-id]")];
  const seen = new Set<string>();
  const uniq = all.filter((p) => !seen.has(p.dataset.id!) && seen.add(p.dataset.id!));
  const picked = range && !range.collapsed ? uniq.filter((p) => range.intersectsNode(p)) : [];
  const use = picked.length ? picked : uniq.slice(0, MAX_WHOLE);
  const paras = use.map((p) => ({ id: p.dataset.id!, text: p.innerText.replace(/\n$/, "") }));
  const filled = picked.length ? paras : paras.filter((p) => p.text.trim());
  // Trang trắng (văn bản mới): gửi đoạn trống đầu tiên để trợ lý có chỗ viết vào — viết thành chữ của đoạn đó + các đoạn sau
  return { paras: filled.length ? filled : paras.slice(0, 1), scope: picked.length ? "selection" : "document" };
}

/** Trợ lý DỰA VÀO ĐÂU (dưới câu trả lời): tài liệu người tích (đọc đủ — hoặc báo rõ mới đọc được bao nhiêu), tài liệu tự tìm trong kho đã dùng. */
export function SourcesLine({ sources, className }: { sources: AssistSource[]; className?: string }) {
  if (!sources.length) return null;
  return (
    <ul className={`t12 flex flex-col gap-1 text-muted-foreground ${className ?? ""}`} aria-label="Nguồn trợ lý đã dựa vào">
      {sources.map((s) => (
        <li key={`${s.kind}:${s.documentId}`} className={`flex items-start gap-1.5 ${s.kind === "picked" && !s.complete ? "text-warn" : ""}`}>
          {s.kind === "found" ? <Search className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            : s.complete ? <FileCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden /> : <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />}
          <span className="min-w-0">
            <span className="t12m">{s.title}</span>
            {s.kind === "found" ? " — tự tìm trong kho"
              : s.complete ? " — đã đọc hết" : ` — tài liệu dài, mới đọc khoảng ${s.percent}% đầu; phần sau trợ lý chưa thấy (tích ít tài liệu hơn để đọc được nhiều hơn)`}
          </span>
        </li>
      ))}
    </ul>
  );
}
