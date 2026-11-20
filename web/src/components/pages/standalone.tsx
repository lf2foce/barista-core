"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { WordEditor, loadWord } from "@/components/blocks/word-doc";
import type { Standalone } from "@/lib/standalone";
import type { WordData } from "@/lib/word";

type State = { kind: "loading" } | { kind: "error"; text: string } | { kind: "word"; data: WordData };

/**
 * Trang riêng ở tab mới: chạy truy vấn nêu trên địa chỉ rồi vẽ khối chính của trang toàn màn hình. Hiện vẽ được khối `word` (trình soạn văn bản);
 * trang khác ⇒ báo mở trong bàn làm việc. Không có bàn làm việc bao quanh nên tự có dòng báo ngắn ở đáy màn.
 */
export function StandalonePage({ target }: { target: Standalone | null }) {
  const [state, setState] = useState<State>(target ? { kind: "loading" } : { kind: "error", text: "Địa chỉ không hợp lệ." });
  const [toast, setToast] = useState<{ text: string; bad: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notify = useCallback((text: string, isError = false) => {
    setToast({ text, bad: isError });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3500);
  }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  useEffect(() => {
    if (!target) return;
    let live = true;
    loadWord(target.query, target.input)
      .then((data) => {
        if (!live) return;
        if (!data || !data.documentId) setState({ kind: "error", text: "Trang này chưa mở riêng ở tab mới được — mở trong bàn làm việc." });
        else {
          document.title = data.title || "Văn bản";
          setState({ kind: "word", data });
        }
      })
      .catch((e: unknown) => live && setState({ kind: "error", text: e instanceof Error ? e.message : "Không mở được trang" }));
    return () => { live = false; };
  }, [target]);

  return (
    <main className="h-dvh overflow-hidden bg-background text-foreground">
      {state.kind === "loading" && <p className="t14 p-6 text-muted-foreground">Đang mở…</p>}
      {state.kind === "error" && (
        <div className="p-6">
          <p className="t14">{state.text}</p>
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- về bàn làm việc bằng tải trang đầy đủ (nạp lại phiên) */}
          <a className="t14m mt-3 inline-block text-primary" href="/">Về bàn làm việc</a>
        </div>
      )}
      {state.kind === "word" && (
<WordEditor initial={state.data} notify={notify} full />
      )}
      {toast && (
        <div role="status" className={`t14 fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-full border border-border bg-surface px-4 py-2 ${toast.bad ? "text-bad" : ""}`}>
          {toast.text}
        </div>
      )}
    </main>
  );
}
