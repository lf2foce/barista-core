"use client";

import { Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { undoOutcome } from "@/lib/autonomy";
import { newKey } from "@/lib/idempotency";
import { shortcutFor } from "@/lib/shortcuts";
import { railSections } from "@/lib/talk";
import { cn } from "@/lib/utils";
import { DialogHost } from "@/components/forms/dialogs";
import { Spinner } from "@/components/primitives";
import { CommandPalette, preloadCharts } from "@/components/blocks/lazy";
import { CanvasView } from "./canvas";
import { ColumnResizer, useColumnWidth } from "./column-width";
import { ChatColumn } from "./chat-column";
import { DeskColumn } from "./desk-column";
import { ChatProvider, useChat } from "./chat-context";
import { DeskProvider, useDesk } from "./desk-context";
import { MessagesColumn, MessagesDetail } from "./messages";
import { Rail } from "./rail";
import { useSolo } from "./solo";
import { WorkspaceColumn } from "./workspace-column";

/**
 * Khung kiểu Lark như app Mac (Rail.swift DeskShell): THANH BÊN TRÁI chọn mục → CỘT GIỮA đổi theo mục (trợ lý / tin nhắn / phân hệ) →
 * KHUNG LÀM VIỆC bên phải giữ nguyên. Dưới 980px: thanh + MỘT ngăn (cột, hoặc khung làm việc có nút quay lại) + nút nổi "Khung làm việc · N".
 */
export function DeskApp() {
  return (
    <DeskProvider>
      <ChatProvider>
        <Shell />
      </ChatProvider>
    </DeskProvider>
  );
}

function Shell() {
  const desk = useDesk();
  const chat = useChat();
  const solo = useSolo();
  const colWidth = useColumnWidth(desk.section.kind === "workspace" ? "menu" : "chat");   // cột chọn màn hẹp hơn cột trò chuyện; kéo mép để đổi
  // phần vẽ biểu đồ không nằm trong JS tải lần đầu; tải sẵn ngay sau lần vẽ đầu, song song với lúc chờ dữ liệu màn chủ (không đợi dữ liệu về rồi mới tải mã)
  useEffect(preloadCharts, []);
  // ⌘K (cmdk) chỉ tải khi mở lần đầu; mở rồi thì giữ để hoạt hoạ đóng / mở lần sau như cũ
  const [paletteOn, setPaletteOn] = useState(false);
  if (desk.paletteOpen && !paletteOn) setPaletteOn(true);

  // phím tắt (đọc trạng thái mới nhất qua ref — gắn một lần)
  const deskRef = useRef(desk);
  useEffect(() => {
    deskRef.current = desk;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const sc = shortcutFor(e);
      if (!sc) return;
      const d = deskRef.current;
      e.preventDefault();
      if (sc.kind === "palette") d.setPaletteOpen(!d.paletteOpen);
      else if (sc.kind === "rail") d.toggleRail();
      else {
        const s = railSections((d.manifest?.workspaces ?? []).map((w) => w.key))[sc.index];
        if (s) d.select(s);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (desk.loadError) {
    return (
      <main className="grid h-full place-items-center p-6">
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <h1 className="t20">Không tải được</h1>
          <p className="t14 text-muted-foreground" role="alert">{desk.loadError}</p>
          <div className="flex gap-2">
            <button type="button" className="t14m h-8 rounded-lg bg-primary px-3 text-primary-foreground" onClick={() => void desk.reloadManifest()}>Thử lại</button>
            <button type="button" className="t14m h-8 rounded-lg border border-border bg-surface px-3" onClick={() => void desk.logout()}>Đăng nhập lại</button>
          </div>
        </div>
      </main>
    );
  }
  if (!desk.manifest) {
    return <main className="grid h-full place-items-center"><Spinner className="size-6" /></main>;
  }
  const showCanvas = desk.narrowShowsCanvas;
  const sec = desk.section;
  // "Tin nhắn" kiểu app chat: cột là danh sách, bên phải là mục đang chọn (thông báo / cuộc trò chuyện) thay cho khung làm việc
  const detail = sec.kind === "messages" && desk.messagesPane.kind !== "list";
  const showRight = showCanvas || detail || desk.canvasMax || !!solo;   // phân hệ một màn: không có cột danh sách, màn dùng trọn khung
  return (
    // điện thoại (< 640px): thanh bên trái 80px thành THANH DƯỚI một hàng cuộn ngang — nội dung lấy lại trọn bề ngang (rà bố cục 18/11)
    <main className="flex h-full min-h-0 overflow-hidden max-sm:flex-col">
      {desk.railHidden || desk.canvasMax ? null : <Rail />}
      <div
        className={cn(
          "relative min-h-0 w-full min-w-0 max-sm:flex-1 min-[980px]:block min-[980px]:w-[var(--col-w)] min-[980px]:shrink-0 min-[980px]:border-r min-[980px]:border-hairline",
          showRight ? "hidden" : "block",
          desk.canvasMax && "min-[980px]:hidden",
          solo && "!hidden",
        )}
        style={{ "--col-w": colWidth.css } as React.CSSProperties}
      >
        {sec.kind === "assistant" ? <ChatColumn /> : sec.kind === "desk" ? <DeskColumn /> : sec.kind === "messages" ? <MessagesColumn /> : <WorkspaceColumn wsKey={sec.key} />}
        <ColumnResizer w={colWidth} />
        {desk.canvas.length > 0 ? (
          <button
            type="button"
            onClick={() => desk.setNarrowShowsCanvas(true)}
            className="t14m absolute right-4 bottom-[76px] z-10 inline-flex items-center gap-2 rounded-full bg-primary px-3.5 py-2.5 text-white shadow-lg min-[980px]:hidden"
          >
            <Layers className="size-4" aria-hidden />
            Khung làm việc · {desk.canvas.length}
          </button>
        ) : null}
      </div>
      <div className={cn("relative min-h-0 min-w-0 flex-1 min-[980px]:block", showRight ? "block" : "hidden")}>
        {detail ? <MessagesDetail /> : <CanvasView />}
      </div>
      <DialogHost />
      {paletteOn ? <CommandPalette /> : null}
      <Toast />
      <span className="sr-only" aria-live="polite">{chat.live ? "Trợ lý đang trả lời" : ""}</span>
    </main>
  );
}

function Toast() {
  const { toast, show, refreshInbox } = useDesk();
  if (!toast) return null;
  const undo = toast.undo;
  // Hoàn tác: chạy `undo.command` với `undo.input` NGUYÊN VĂN bằng quyền người bấm (việc đã được sửa sau đó ⇒ server từ chối, không đè)
  const run = async () => {
    if (!undo) return;
    try {
      const r = await api.command(undo.command, undo.input, newKey());
      const st = undoOutcome(r.status, r.error);
      show(st.message, st.phase === "failed");
      if (st.phase === "undone") void refreshInbox();
    } catch (e) {
      show(e instanceof Error ? e.message : "Không hoàn tác được", true);
    }
  };
  return (
    <div
      role="status"
      className={cn("t14m fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-full px-4 py-2 shadow-lg", toast.isError ? "bg-bad text-white" : "border border-border bg-popover text-popover-foreground")}
    >
      {toast.text}
      {undo ? (
        <button type="button" onClick={() => void run()} className="t14m rounded-md text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">
          Hoàn tác
        </button>
      ) : null}
    </div>
  );
}
