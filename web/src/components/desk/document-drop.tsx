"use client";

import { FileText, MessageSquare, Paperclip, Sparkles, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { actionClass } from "@/components/primitives";
import { api, type ChatDocument } from "@/lib/api";
import {
  CHAT_DOC_MAX, decodeShelves, decodeTypeHint, defaultDropChoice, documentTitle, dropChoices, toChatDocument, uploadOutcome, uploadedPrompt,
  type DropChoice, type TypeHint, type UploadShelf, type Uploaded,
} from "@/lib/documents";
import { newKey } from "@/lib/idempotency";
import { cn } from "@/lib/utils";
import { useDesk } from "./desk-context";

/** Một tệp đã lưu. ``hint``: undefined = đang đọc; null = không lấy được gợi ý. */
type Row = Uploaded & { hint?: TypeHint | null };

const READERS = 3;   // đọc loại song song vừa phải: chip hiện dần, không phải đợi cả bộ

/** Tài liệu thả vào chat TRỢ LÝ (ADR 0249) ⇒ hộp này: KHÔNG tự vào kho tri thức. Máy chọn sẵn (người xác nhận — 0225): đang mở một hồ sơ nhận tệp ⇒
 *  "Gắn vào hồ sơ"; không thì "Chỉ dùng trong cuộc trò chuyện này" (tài liệu đi kèm câu hỏi tiếp theo, thân tệp không lưu). "Đăng lên kho tri thức"
 *  chỉ khi người chọn rõ — khi đó NGƯỜI chọn kệ (thấy "ai đọc được"), không chọn sẵn kệ nào; tải xong hiện chip loại chứng từ server soạn, chỉ tài
 *  liệu ĐÃ LƯU vào câu mời trợ lý; chờ duyệt / lỗi báo riêng. */
export function DocumentDropDialog({ files, onClose, onKeep }: { files: File[]; onClose: () => void; onKeep: (docs: ChatDocument[]) => void }) {
  const desk = useDesk();
  const record = desk.openRecord;
  const [choice, setChoice] = useState<DropChoice>(() => defaultDropChoice(record));
  const guessed = defaultDropChoice(record);
  const [shelves, setShelves] = useState<UploadShelf[] | null>(null);
  const [shelf, setShelf] = useState("");
  const [busy, setBusy] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [pending, setPending] = useState<string[]>([]);
  const [failed, setFailed] = useState<string[]>([]);
  const stop = useRef<AbortController | null>(null);
  const closed = useRef(false);

  useEffect(() => {
    if (choice !== "knowledge" || shelves !== null) return;
    api.queryRaw<unknown>("kb.upload_shelves").then((r) => setShelves(decodeShelves(r)), () => setShelves([]));
  }, [choice, shelves]);

  /** Gắn vào hồ sơ đang mở — cùng cửa với khối "Tệp" của thẻ (server kiểm quyền, cỡ, loại). */
  const attach = async () => {
    if (!record) return;
    let done = 0;
    const bad: string[] = [];
    for (const [i, f] of files.entries()) {
      setBusy(`Đang gắn ${i + 1}/${files.length}: ${f.name}…`);
      try {
        const r = await api.recordFileUpload(record.path, f, { subject_kind: record.subjectKind, subject_id: record.subjectId }, newKey());
        if (r.status === "ok") done++;
        else bad.push(f.name);
      } catch {
        bad.push(f.name);
      }
    }
    setBusy("");
    onClose();
    if (bad.length) desk.show(`Không gắn được: ${bad.join(", ")}`, true);
    if (done) {
      desk.show(`Đã gắn ${done} tệp vào “${record.title}”`);
      desk.bumpRecordFiles();
    }
  };

  /** Chỉ cho cuộc trò chuyện này: tài liệu đi kèm câu hỏi tiếp theo (người gõ câu hỏi rồi gửi). */
  const keep = async () => {
    const out: ChatDocument[] = [];
    for (const f of files.slice(0, CHAT_DOC_MAX)) {
      try {
        out.push(await toChatDocument(f));
      } catch (e) {
        desk.show(e instanceof Error ? e.message : `Không đọc được ${f.name}`, true);
      }
    }
    if (files.length > CHAT_DOC_MAX) desk.show(`Tối đa ${CHAT_DOC_MAX} tài liệu mỗi câu hỏi — bỏ bớt ${files.length - CHAT_DOC_MAX}`, true);
    onClose();
    if (out.length) onKeep(out);
  };

  /** Đóng hộp + đưa câu mời vào ô soạn tin — người sửa rồi bấm gửi. */
  const finish = (done: Row[], wait: string[], bad: string[]) => {
    closed.current = true;
    onClose();
    if (wait.length) desk.show(`Chờ duyệt, chưa vào kho: ${wait.join(", ")}`);
    if (bad.length) desk.show(`Không tải lên được: ${bad.join(", ")}`, true);
    if (done.length) {
      if (!wait.length && !bad.length) desk.show(`Đã lưu ${done.length} chứng từ vào kho`);
      const hints = Object.fromEntries(done.flatMap((r) => (r.hint ? [[r.documentId, r.hint] as const] : [])));
      desk.ask(uploadedPrompt(done.map(({ title, documentId }) => ({ title, documentId })), hints));
    }
  };

  const upload = async () => {
    const saved: Row[] = [];
    const wait: string[] = [];
    const bad: string[] = [];
    const ctl = new AbortController();
    stop.current = ctl;
    for (const [i, f] of files.entries()) {
      if (ctl.signal.aborted) break;
      setBusy(`Đang tải lên ${i + 1}/${files.length}: ${f.name}…`);
      try {
        const out = uploadOutcome(await api.kbUpload(f, { title: documentTitle(f.name), shelf, kind: "other" }, ctl.signal));
        if (out.kind === "saved") saved.push({ title: documentTitle(f.name), documentId: out.documentId });
        else (out.kind === "pending" ? wait : bad).push(f.name);
      } catch {
        if (!ctl.signal.aborted) bad.push(f.name);
      }
    }
    stop.current = null;
    setBusy("");
    setPending(wait);
    setFailed(bad);
    if (ctl.signal.aborted && !saved.length) return onClose();
    setRows(saved);

    // Đọc loại SAU khi lưu (cần mã tài liệu): server soạn sẵn chip; từng tệp xong là hiện. Lỗi ⇒ không chip, không chặn.
    let next = 0;
    const done = [...saved];
    const worker = async () => {
      while (next < done.length && !closed.current) {
        const i = next++;
        let hint: TypeHint | null = null;
        try {
          hint = decodeTypeHint(await api.queryRaw<unknown>("kb.suggest_type", { document_id: done[i].documentId }));
        } catch {
          hint = null;
        }
        done[i] = { ...done[i], hint };
        if (!closed.current) setRows([...done]);
      }
    };
    await Promise.all(Array.from({ length: READERS }, worker));
    if (!closed.current && !done.some((r) => r.hint?.chip) && !wait.length && !bad.length) finish(done, wait, bad);
  };

  const chosen = shelves?.find((s) => s.value === shelf);
  const reading = rows?.some((r) => r.hint === undefined) ?? false;

  const chip = (r: Row) => {
    if (r.hint === undefined) return <span className="t12 shrink-0 text-muted-foreground">đang đọc…</span>;
    if (!r.hint?.chip) return null;
    const warn = r.hint.tone === "warn";
    return (
      <span className={cn("t12m shrink-0 rounded-full border px-2.5 py-0.5", warn ? "border-border" : "border-primary/40 bg-primary/5")}
            style={warn ? { color: "var(--warn)" } : undefined}>
        {r.hint.chip}
      </span>
    );
  };

  return (
    <Dialog open onOpenChange={(o) => !o && !busy && (rows ? finish(rows, pending, failed) : onClose())}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="t16">{rows ? `Đã lưu ${rows.length}/${files.length} chứng từ` : `Dùng ${files.length} tài liệu vào việc gì?`}</DialogTitle>
          <DialogDescription className="t12">
            {rows ? "Loại do hệ thống đọc từ nội dung và đề xuất. Tệp ghi “chưa chắc” sẽ được hỏi lại, không tự xếp loại."
              : choice === "knowledge" ? "Chọn kệ — ai đọc được kệ đó sẽ thấy các chứng từ này. Xong em điền sẵn câu mời trợ lý."
              : "Em chọn sẵn theo việc anh/chị đang làm — đổi được trước khi bấm."}
          </DialogDescription>
        </DialogHeader>

        {rows ? (
          <ul className="flex flex-col gap-2">
            {rows.map((r) => (
              <li key={r.documentId} className="flex items-center gap-2">
                <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="t14 min-w-0 flex-1 truncate">{r.title}</span>
                {chip(r)}
              </li>
            ))}
            {pending.map((n) => <li key={`p-${n}`} className="t12" style={{ color: "var(--warn)" }}>{n} — chờ duyệt, chưa vào kho</li>)}
            {failed.map((n) => <li key={`f-${n}`} className="t12" style={{ color: "var(--bad)" }}>{n} — không tải lên được</li>)}
          </ul>
        ) : (
          <>
            <ul className="flex flex-col gap-1">
              {files.map((f) => (
                <li key={f.name} className="t14 flex items-center gap-2"><FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />{f.name}</li>
              ))}
            </ul>
            <div role="radiogroup" aria-label="Dùng tài liệu vào việc gì" className="flex flex-col gap-1.5">
              {dropChoices(record).map((c) => {
                const Icon = c.value === "record" ? Paperclip : c.value === "conversation" ? MessageSquare : Users;
                return (
                  <button key={c.value} type="button" role="radio" aria-checked={choice === c.value} disabled={!!busy} onClick={() => setChoice(c.value)}
                          className={cn("flex items-start gap-2 rounded-lg border p-2.5 text-left", choice === c.value ? "border-primary bg-primary/5" : "border-border")}>
                    <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="t14m">{c.label}</span>
                      <span className="t12 text-muted-foreground">{c.detail}</span>
                    </span>
                    {c.value === guessed ? <span className="t12 inline-flex shrink-0 items-center gap-1 text-primary"><Sparkles className="size-3" aria-hidden />chọn sẵn</span> : null}
                  </button>
                );
              })}
            </div>
            {choice === "knowledge" ? (
            <div role="radiogroup" aria-label="Kệ lưu" className="flex flex-col gap-1.5">
              {shelves === null ? <p className="t12 text-muted-foreground">Đang tải danh sách kệ…</p> : null}
              {shelves?.length === 0 ? <p className="t12" style={{ color: "var(--bad)" }}>Bạn chưa thêm được tài liệu vào kệ nào.</p> : null}
              {shelves?.map((s) => (
                <button key={s.value} type="button" role="radio" aria-checked={shelf === s.value} disabled={!!busy} onClick={() => setShelf(s.value)}
                        className={cn("flex items-start gap-2 rounded-lg border p-2.5 text-left", shelf === s.value ? "border-primary bg-primary/5" : "border-border")}>
                  <Users className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="flex flex-col gap-0.5">
                    <span className="t14m">{s.label}</span>
                    <span className="t12 text-muted-foreground">Ai đọc được: {s.audience}</span>
                  </span>
                </button>
              ))}
            </div>
            ) : null}
            {choice === "knowledge" && chosen?.everyone ? <p className="t12" style={{ color: "var(--warn)" }}>Kệ này cả công ty đọc được.</p> : null}
          </>
        )}

        <DialogFooter>
          {busy ? <span className="t12 mr-auto text-muted-foreground">{busy}</span> : reading ? <span className="t12 mr-auto text-muted-foreground">Đang đọc loại chứng từ…</span> : null}
          {rows ? (
            <button type="button" className={actionClass("primary")} onClick={() => finish(rows, pending, failed)}>Xong</button>
          ) : (
            <>
              <button type="button" className={actionClass("default")} onClick={() => (busy ? stop.current?.abort() : onClose())}>{busy ? "Dừng tải" : "Huỷ"}</button>
              <button type="button" className={actionClass("primary")} disabled={!!busy || (choice === "knowledge" && !shelf)}
                      onClick={() => void (choice === "record" ? attach() : choice === "conversation" ? keep() : upload())}>
                {choice === "record" ? "Gắn vào hồ sơ" : choice === "conversation" ? "Dùng trong cuộc trò chuyện" : "Đăng lên kho"}
              </button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
