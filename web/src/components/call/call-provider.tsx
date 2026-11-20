"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { CallHub } from "@/lib/call-hub";
import { CallDock } from "./call-dock";

// Gắn MỘT lần ở khung ngoài cùng (app/layout.tsx), trên cả cây trang: phiên gọi sống ở đây nên đổi trang (Next điều hướng phía trình duyệt) không làm rớt
// cuộc gọi. Bảng nổi (call-dock.tsx) vẽ phiên hiện tại; nút Gọi / Tham gia gọi `useCallHub()?.openPage(...)`.

const HubContext = createContext<CallHub | null>(null);

export function CallProvider({ children }: { children: React.ReactNode }) {
  const [hub] = useState(() => new CallHub());
  // cho kiểm thử trình duyệt (ảnh bảng gọi): mở bảng nổi không cần đi qua thẻ / nút trao đổi — như `__baristaCall` của bộ máy. Trong hiệu ứng, không trong
  // hàm khởi tạo: chế độ Strict của React gọi hàm khởi tạo hai lần và bỏ một bản.
  useEffect(() => {
    (window as unknown as { __baristaHub?: CallHub }).__baristaHub = hub;
  }, [hub]);
  return (
    <HubContext.Provider value={hub}>
      {children}
      <CallDock hub={hub} />
    </HubContext.Provider>
  );
}

/** null khi chạy ngoài khung (vd trang thử riêng) — người gọi rơi về mở trang gọi ở thẻ riêng. */
export function useCallHub(): CallHub | null {
  return useContext(HubContext);
}
