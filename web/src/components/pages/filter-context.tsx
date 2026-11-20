"use client";

import { createContext, useContext } from "react";

// Lọc chéo kiểu báo cáo BI (0173): màn cho các biểu đồ trong thẻ ĐỌC / ĐẶT bộ lọc của màn — bấm một cột / miếng ⇒ bộ lọc đó = giá trị của nó, truy vấn chạy lại.
// Ngoài màn (thẻ trong chat…) không có ngữ cảnh ⇒ biểu đồ không bấm được.
export type ScreenFilters = { get: (param: string) => string | undefined; set: (param: string, value: string | undefined) => void };

export const ScreenFiltersContext = createContext<ScreenFilters | null>(null);
export const useScreenFilters = () => useContext(ScreenFiltersContext);
