"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Input, Page } from "@/lib/types";

/** Tải một trang (bảng / chi tiết / thẻ) của truy vấn; đổi `input` hoặc gọi `reload` ⇒ tải lại. `initialPage`: bản chụp có sẵn (thẻ trợ lý). */
/** `paged`: bảng xin trang phía server (`PAGE_ROWS` dòng / lần) — tắt khi đang ở góc nhìn cần đủ dòng (Kanban, lịch, dòng thời gian). */
export function usePage(query: string, input: Input, initialPage?: Page | null, paged = true) {
  const [page, setPage] = useState<Page | null>(initialPage ?? null);
  const [error, setError] = useState<string | null>(null);
  const [errorAt, setErrorAt] = useState<number | null>(null);   // giờ lỗi — màn lỗi ghi "lúc 14:05" để người báo đúng lần hỏng
  const [loading, setLoading] = useState(!initialPage);
  const seed = useRef(!!initialPage);
  const seq = useRef(0);
  const key = `${query}|${JSON.stringify(input)}|${paged}`;
  const pagedRef = useRef(paged);
  const inputRef = useRef(input);
  useEffect(() => {
    inputRef.current = input;
    pagedRef.current = paged;
  });

  const reload = useCallback(async () => {
    const n = ++seq.current;
    setLoading(true);
    try {
      const p = await api.page(query, inputRef.current, undefined, pagedRef.current ? { offset: 0, limit: PAGE_ROWS } : undefined);
      if (n !== seq.current) return;
      setPage(p);
      setError(null);
    } catch (e) {
      if (n !== seq.current) return;
      setError(e instanceof Error ? e.message : "Không tải được");
      setErrorAt(Date.now());
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    if (seed.current) {
      seed.current = false;
      return;
    }
    void reload();
  }, [key, reload]);

  /** Trang kế của bảng (trang phía server): tải `PAGE_ROWS` dòng tiếp theo, nối vào bảng đang thấy. Lọc / tải lại giữa chừng ⇒ bỏ kết quả cũ. */
  const more = useCallback(async () => {
    const cur = page;
    if (cur?.kind !== "table" || !cur.paging) return;
    const n = seq.current;
    const next = await api.page(query, inputRef.current, undefined, { offset: cur.rows.length, limit: PAGE_ROWS });
    if (n !== seq.current || next.kind !== "table") return;
    const seen = new Set(cur.rows.map((r) => r.id));
    setPage({ ...cur, rows: [...cur.rows, ...next.rows.filter((r) => !seen.has(r.id))], paging: next.paging ?? cur.paging });
  }, [page, query]);

  return { page, error, errorAt, loading, reload, more };
}

/** Số dòng một trang của bảng (server cắt — ô số vẫn tính trên cả tập, Excel xuất cả tập). */
export const PAGE_ROWS = 300;
