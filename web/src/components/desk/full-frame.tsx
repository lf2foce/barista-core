"use client";

import { RefreshCw } from "lucide-react";
import { createContext, useContext } from "react";
import { cn } from "@/lib/utils";
import { PANE_HEADER, RailToggle } from "./column";
import { useDesk } from "./desk-context";
import { ScreensMenu } from "./screens-menu";
import { useFullScreen, useSolo } from "./solo";

/**
 * Màn đang chiếm TRỌN khung làm việc (phân hệ chỉ có một màn — `useSolo`): khung không vẽ đầu riêng, màn tự vẽ các ngăn của mình (vd Soạn với AI vẽ y như
 * màn Trợ lý: ngăn trò chuyện + khung bản soạn) và mang nút ẩn / hiện thanh bên trái. Ngoài khung (hộp thoại) ⇒ false.
 */
const FullFrame = createContext(false);

export function FullFrameProvider({ children }: { children: React.ReactNode }) {
  return <FullFrame.Provider value>{children}</FullFrame.Provider>;
}

export function useFullFrame(): boolean {
  return useContext(FullFrame);
}

/**
 * Màn THƯỜNG (bảng, lịch, thẻ…) của phân hệ một màn: khung để trần là dành cho màn tự vẽ ngăn (Soạn với AI) — màn thường vẫn cần đầu khung (tên phân
 * hệ, ẩn / hiện thanh bên trái, tải lại) và thẻ bọc có lề như mọi màn khác, không thì nội dung dính sát mép và nút cuối hàng bị cắt.
 */
export function SoloScreenFrame({ children }: { children: React.ReactNode }) {
  const desk = useDesk();
  // phân hệ một màn ⇒ tên phân hệ; màn THƯỜNG tự khai ``full_frame`` trong phân hệ nhiều màn (vd Lịch) ⇒ tên màn là menu sang các màn khác (ScreensMenu)
  const only = useSolo();
  const full = useFullScreen();
  const solo = only ?? full;
  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <header className={cn(PANE_HEADER, "px-3 min-[980px]:px-6")}>
        <RailToggle />
        <div className="min-w-0 flex-1">{only ? <h2 className="t16 truncate">{only.ws.title}</h2> : <ScreensMenu title={full?.screen.title ?? ""} />}</div>
        {solo ? (
          <button type="button" onClick={() => desk.refreshItem(solo.id)} className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label="Tải lại" title="Tải lại">
            <RefreshCw className="size-4" />
          </button>
        ) : null}
      </header>
      {/* thẻ VỪA một màn hình (rà UI 21/11): phần tự cuộn bên trong (lịch, bảng) có chiều cao giới hạn nên cuộn trong thẻ, không đội thẻ cao rồi cuộn cả trang;
          nội dung thường dài hơn màn hình thì chính thẻ cuộn. Lề trên nhỏ để thẻ sát tiêu đề. */}
      <div className="min-h-0 flex-1 overflow-hidden px-3 pb-3 pt-0.5 min-[980px]:px-4 min-[980px]:pb-4">
        <section className="flex h-full min-h-0 min-w-0 flex-col gap-3 overflow-y-auto rounded-[20px] border border-border bg-surface p-3.5 min-[980px]:p-[18px]" aria-label={solo?.screen.title}>
          {children}
        </section>
      </div>
    </div>
  );
}

