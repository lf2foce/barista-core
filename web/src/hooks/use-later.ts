"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Block, CardPage } from "@/lib/types";

type Got = Record<number, Block[] | { error: number }>;

/**
 * Khối tải sau (ui.b_later): thẻ về trước với phần nhanh (ô số, biểu đồ chính); mỗi khối `later` được thay bằng các khối của truy vấn nó trỏ.
 * Tải LẦN LƯỢT theo thứ tự khối trong thẻ — thứ tự là ưu tiên do server khai; không bắn song song để phần nặng không giành máy chủ với màn kế.
 * Khối chưa về giữ nguyên (bộ vẽ hiện chỗ chờ); lỗi ⇒ một dòng báo, phần còn lại của thẻ vẫn dùng được. Thẻ mới (lọc lại / tải lại) ⇒ tải lại.
 */
export function useLater(page: CardPage): CardPage {
  const [state, setState] = useState<{ page: CardPage; got: Got }>({ page, got: {} });
  const got = state.page === page ? state.got : {};
  useEffect(() => {
    const later = page.blocks.flatMap((b, i) => (b.type === "later" ? [{ i, b }] : []));
    if (!later.length) return;
    let alive = true;
    void (async () => {
      for (const { i, b } of later) {
        if (b.type !== "later") continue;
        let blocks: Block[] | { error: number };
        try {
          const p = b.part ? await api.part(b.query, b.input, b.part) : await api.query(b.query, b.input);   // không ?later=1: server ghép đủ phần lồng bên trong
          blocks = p.kind === "card" ? p.blocks : [];
        } catch {
          blocks = { error: Date.now() };
        }
        if (!alive) return;
        setState((s) => ({ page, got: { ...(s.page === page ? s.got : {}), [i]: blocks } }));
      }
    })();
    return () => {
      alive = false;
    };
  }, [page]);
  if (!page.blocks.some((b) => b.type === "later")) return page;
  const blocks = page.blocks.flatMap((b, i): Block[] => {
    if (b.type !== "later") return [b];
    const g = got[i];
    if (g === undefined) return [b];
    // lỗi là tin cần thấy (không phải chú thích) ⇒ tông warn: không bị gấp như ghi chú nhạt; kèm giờ lỗi để báo đúng lần hỏng
    if (!Array.isArray(g)) return [{ type: "callout", tone: "warn", title: b.title || "Phần này chưa tải được", text: `Tạm thời chưa tải được phần này (lúc ${clock(new Date(g.error))}) — bấm Tải lại ở đầu thẻ sau ít phút.`, facts: [], draft: "", actions: [] }];
    return g;
  });
  return { ...page, blocks };
}
