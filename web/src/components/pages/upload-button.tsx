"use client";

import { FileUp } from "lucide-react";
import { useRef, useState } from "react";
import { useDesk } from "@/components/desk/desk-context";
import { actionClass, Spinner } from "@/components/primitives";
import { api, ApiError } from "@/lib/api";
import type { ActionSpec } from "@/lib/types";

/** Nút của lệnh NHẬN TỆP (ADR 0262): chọn NHIỀU tệp ⇒ gửi lần lượt từng tệp vào `POST /v1/uploads/{lệnh}` (quyền, chống lặp, nhật ký ở server), rồi báo gọn
 *  "Đã nhận N tệp" + tệp lỗi kèm lý do. Đuôi nhận do server khai (`upload`); `preset` của nút đi kèm làm ô thường. Không đọc / đoán gì ở trình duyệt. */
export function UploadButton({ a, onDone }: { a: ActionSpec; onDone: () => void }) {
  const desk = useDesk();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<{ done: number; total: number } | null>(null);
  const accept = (a.upload ?? "").split(",").map((x) => `.${x}`).join(",");
  const params = Object.fromEntries(Object.entries(a.preset ?? {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => [k, String(v)]));

  async function send(files: File[]) {
    if (!files.length) return;
    setBusy({ done: 0, total: files.length });
    const failed: string[] = [];
    let ok = 0;
    for (const [i, f] of files.entries()) {
      try {
        const r = await api.uploadFile(a.command, f, params);
        if (r.status === "ok" || r.status === "pending_approval" || r.status === "proposed") ok += 1;
        else failed.push(`${f.name}: ${r.error || "không nhận"}`);
      } catch (e) {
        failed.push(`${f.name}: ${e instanceof ApiError ? e.message : "lỗi mạng"}`);
      }
      setBusy({ done: i + 1, total: files.length });
    }
    setBusy(null);
    if (input.current) input.current.value = "";
    desk.show(failed.length ? `Đã nhận ${ok}/${files.length} tệp — ${failed.join("; ")}` : `Đã nhận ${ok} tệp`, failed.length > 0);
    if (ok) onDone();
  }

  return (
    <>
      <input ref={input} type="file" multiple accept={accept} className="hidden" onChange={(e) => void send([...(e.target.files ?? [])])} aria-hidden tabIndex={-1} />
      <button type="button" className={actionClass(a.style)} disabled={!!busy} onClick={() => input.current?.click()} title={`Nhận tệp ${accept.replaceAll(",", ", ")}`}>
        {busy ? <Spinner className="size-3.5" /> : <FileUp className="size-3.5" aria-hidden />}
        {busy ? `Đang gửi ${busy.done}/${busy.total}…` : a.title}
      </button>
    </>
  );
}
