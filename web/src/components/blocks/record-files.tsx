"use client";

import { Paperclip } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ListBlock } from "@/components/blocks/misc";
import { useDesk } from "@/components/desk/desk-context";
import { actionClass } from "@/components/primitives";
import { api } from "@/lib/api";
import { newKey } from "@/lib/idempotency";
import type { Input, ListAttach, ListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Khối "Tệp" của hồ sơ (ADR 0249): danh sách server vẽ sẵn + gắn tệp (nút "Thêm tệp" hoặc thả tệp lên khối). Khi hiện, khối báo cho bàn làm việc
 *  biết hồ sơ nào đang mở — tài liệu thả vào chat trợ lý mặc định gắn vào đây (người xác nhận). Quyền, cỡ, loại tệp do server kiểm. */
export function RecordFilesBlock({ title, items, attach, onOpen, onAsk, onChanged }: {
  title: string; items: ListItem[]; attach: ListAttach;
  onOpen: (q: string, input: Input, title?: string) => void; onAsk: (q: string) => void; onChanged?: () => void;
}) {
  const desk = useDesk();
  const { setOpenRecord, recordFilesRev } = desk;
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState("");
  const [over, setOver] = useState(false);
  const [label, setLabel] = useState("");
  const seenRev = useRef(recordFilesRev);

  useEffect(() => {
    setOpenRecord({ subjectKind: attach.subject_kind, subjectId: attach.subject_id, title: attach.subject_title || "hồ sơ này", path: attach.path });
    return () => setOpenRecord(null, attach.subject_id);
  }, [setOpenRecord, attach.subject_kind, attach.subject_id, attach.subject_title, attach.path]);

  useEffect(() => {   // tệp vừa gắn từ chat trợ lý ⇒ thẻ đang mở tải lại
    if (recordFilesRev !== seenRev.current) {
      seenRev.current = recordFilesRev;
      onChanged?.();
    }
  }, [recordFilesRev, onChanged]);

  const upload = async (files: File[]) => {
    if (!files.length || busy) return;
    let done = 0;
    for (const [i, f] of files.entries()) {
      setBusy(`Đang gắn ${i + 1}/${files.length}: ${f.name}…`);
      try {
        const r = await api.recordFileUpload(attach.path, f, { subject_kind: attach.subject_kind, subject_id: attach.subject_id, label }, newKey());
        if (r.status === "ok") done++;
        else desk.show(r.error || `Không gắn được ${f.name}`, true);
      } catch (e) {
        desk.show(e instanceof Error ? e.message : `Không gắn được ${f.name}`, true);
      }
    }
    setBusy("");
    if (done) {
      desk.show(`Đã gắn ${done} tệp vào “${attach.subject_title || "hồ sơ"}”`);
      onChanged?.();
    }
  };

  return (
    <section
      className={cn("flex flex-col gap-2 rounded-xl", over && "outline-2 outline-dashed outline-primary")}
      onDragOver={(e) => {
        if (!Array.from(e.dataTransfer.types).includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        if (!over) setOver(true);
      }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false); }}
      onDrop={(e) => {
        if (!Array.from(e.dataTransfer.types).includes("Files")) return;
        e.preventDefault();
        setOver(false);
        void upload(Array.from(e.dataTransfer.files));
      }}
    >
      <div className="flex items-center gap-2">
        <h3 className="t16 flex-1">{title}</h3>
        <input ref={input} type="file" multiple hidden onChange={(e) => { void upload(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <button type="button" className={cn(actionClass("default"), "h-7 text-[13px]")} disabled={!!busy} onClick={() => input.current?.click()}
                title={`Nhận ${attach.file_kinds}`}>
          <Paperclip className="size-3.5" aria-hidden />Thêm tệp
        </button>
      </div>
      {attach.labels.length ? (
        <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Loại tệp (không bắt buộc)">
          <span className="t12 text-muted-foreground">Loại tệp (không bắt buộc):</span>
          {attach.labels.map((l) => (
            <button key={l} type="button" role="radio" aria-checked={label === l} onClick={() => setLabel(label === l ? "" : l)}
                    className={cn("t12m rounded-full border px-2.5 py-0.5", label === l ? "border-primary bg-primary/5 text-primary" : "border-border")}>
              {l}
            </button>
          ))}
        </div>
      ) : null}
      <ListBlock items={items} onOpen={onOpen} onAsk={onAsk} />
      {busy ? <p className="t12 text-muted-foreground">{busy}</p> : <p className="t12 text-muted-foreground">Thả tệp vào đây để gắn vào hồ sơ — ai thấy hồ sơ thì thấy tệp.</p>}
    </section>
  );
}
