"use client";

import { Check, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { undoOutcome } from "@/lib/autonomy";
import { newKey } from "@/lib/idempotency";
import type { ChatCard } from "@/lib/types";
import { cn } from "@/lib/utils";
import { actionClass } from "@/components/primitives";
import { BriefView, SettledLine } from "./brief";
import { useDesk } from "./desk-context";
import { useChat } from "./chat-context";

/**
 * Đề xuất MỘT CHẠM (0144): nút Xác nhận nằm ngay trong câu trả lời, kèm vì sao việc này chờ người (`why` server viết từ `decide`). Xác nhận chạy lệnh
 * bằng quyền NGƯỜI qua `proposals/decide` rồi trợ lý làm tiếp; "Sửa" mở thẻ đầy đủ trên khung làm việc để chỉnh ô trước khi xác nhận.
 */
export function ProposalRow({ card, onEdit }: { card: ChatCard; onEdit?: () => void }) {
  const desk = useDesk();
  const chat = useChat();
  const [busy, setBusy] = useState<"confirm" | "dismiss" | null>(null);
  const [error, setError] = useState("");
  const done = desk.settled(card);
  const edited = desk.proposalEdits[card.id] !== undefined;

  async function decide(d: "confirm" | "dismiss") {
    setBusy(d);
    setError("");
    try {
      const input = d === "confirm" ? desk.proposalEdits[card.id] ?? card.input ?? {} : {};
      // MỘT đường quyết cho mọi nơi: thẻ buổi sáng quyết qua morning/{id}/decide, thẻ trong lượt chat qua proposals/decide
      const o = card.source === "morning" ? await chat.decideMorning(card, d, input, newKey()) : await chat.decide(card, d, input, newKey());
      if (o.done) {
        desk.setConfirmed(card.id, o.message, o.approvalId, o.status);
        void desk.refreshInbox();
      } else setError(o.message || o.status);
    } finally {
      setBusy(null);
    }
  }

  const brief = card.brief;
  return (
    <div className={cn("flex flex-col gap-2 rounded-[14px] border px-3 py-2.5", done ? "border-border bg-surface" : "border-primary/40 bg-primary/[0.04]")}>
      <div className="flex items-start gap-2.5">
        <Sparkles className="mt-0.5 size-4 shrink-0" style={{ color: "#af52de" }} aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="t14m">{card.title}</span>
          {brief ? <BriefView brief={brief} /> : card.subtitle && card.subtitle !== card.title ? <span className="t12 line-clamp-2 text-muted-foreground">{card.subtitle}</span> : null}
        </div>
      </div>
      {error ? <p className="t12 text-bad" role="alert">{error}</p> : null}
      {done ? (
        <>
          <SettledLine settled={done} />
          <ResolveActions card={card} />
        </>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy !== null} onClick={() => void decide("confirm")} className={cn(actionClass("primary"), "h-8 px-3")}>
            <Check className="size-3.5" aria-hidden />
            {busy === "confirm" ? "Đang làm…" : edited ? "Xác nhận bản đã sửa" : "Xác nhận"}
          </button>
          <button type="button" disabled={busy !== null} onClick={() => (onEdit ? onEdit() : desk.openCard(card, true))} className={cn(actionClass("default"), "h-8 px-3")}>
            Xem, sửa
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void decide("dismiss")} className="t12m h-8 rounded-md px-2 text-muted-foreground hover:bg-accent disabled:opacity-50">
            {busy === "dismiss" ? "Đang bỏ…" : "Bỏ"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * ĐỐI SOÁT (rà 22/11): thẻ cần đối soát (gọi hệ ngoài lỗi lưng chừng / quá hạn) — người xem hệ ngoài rồi chọn một nút SERVER gửi (`card.resolve`).
 * Không gọi ra ngoài lần nào; "đã thực hiện" đóng thẻ là xong, "chưa thực hiện" đóng thẻ là lỗi (muốn làm thì đề xuất mới).
 */
export function ResolveActions({ card }: { card: ChatCard }) {
  const desk = useDesk();
  const chat = useChat();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const resolve = desk.settled(card)?.resolve ?? [];
  if (!resolve.length) return null;
  async function pick(decision: string) {
    setBusy(decision);
    setError("");
    try {
      const o = card.source === "morning" ? await chat.decideMorning(card, decision, {}, newKey()) : await chat.decide(card, decision, {}, newKey());
      if (o.done) {
        desk.setConfirmed(card.id, o.message, o.approvalId, o.status);
        void desk.refreshInbox();
      } else setError(o.message || o.status);
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="t12 text-muted-foreground">Kiểm tra trên hệ ngoài rồi chọn đúng điều đã xảy ra — Barista không gửi lại lệnh.</p>
      {error ? <p className="t12 text-bad" role="alert">{error}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        {resolve.map((r, i) => (
          <button key={r.decision} type="button" disabled={busy !== ""} onClick={() => void pick(r.decision)} className={cn(actionClass(i === 0 ? "primary" : "default"), "h-8 px-3")}>
            {busy === r.decision ? "Đang ghi…" : r.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Thẻ "trợ lý đã tự làm" (0144): Hoàn tác chạy `undo.command` với `undo.input` NGUYÊN VĂN bằng quyền NGƯỜI bấm qua cửa lệnh thường (nhật ký
 * thường) — người không có quyền thì server từ chối; việc đã được sửa sau đó ⇒ server trả conflict, thẻ giữ kết quả và hiện lời báo.
 */
export function ResultRow({ card }: { card: ChatCard }) {
  const desk = useDesk();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const undone = desk.confirmed[card.id]?.message;
  const undo = card.undo;

  async function run() {
    if (!undo) return;
    setRunning(true);
    setError("");
    try {
      const r = await api.command(undo.command, undo.input, newKey());   // khoá mới mỗi lần bấm: lần trước conflict thì bấm lại là yêu cầu mới
      const st = undoOutcome(r.status, r.error);
      if (st.phase === "undone") {
        desk.setConfirmed(card.id, st.message);
        void desk.refreshInbox();
      } else setError(st.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không hoàn tác được");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5 rounded-[14px] border border-border bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={cn("grid size-5 shrink-0 place-items-center rounded-full text-white", undone ? "bg-muted-foreground/60" : "bg-ok")} aria-hidden>
          {undone ? <RotateCcw className="size-3" strokeWidth={3} /> : <Check className="size-3" strokeWidth={3} />}
        </span>
        <span className="flex min-w-[170px] flex-1 flex-col">
          <span className={cn("t14m line-clamp-2", undone && "text-muted-foreground line-through decoration-muted-foreground/50")}>{card.title}</span>
          <span className={cn("t12", undone ? "text-ok" : "text-muted-foreground")}>{undone || card.subtitle || "Trợ lý đã tự làm"}</span>
        </span>
        {undo && !undone ? (
          <button type="button" disabled={running} onClick={() => void run()} title={undo.title} className={cn(actionClass("default"), "h-8 shrink-0 px-3")}>
            <RotateCcw className="size-3.5" aria-hidden />
            {running ? "Đang hoàn tác…" : "Hoàn tác"}
          </button>
        ) : null}
      </div>
      {error ? <p className="t12 text-bad" role="alert">{error}</p> : null}
    </div>
  );
}
