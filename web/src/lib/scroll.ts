/** Bám đáy khi stream (kiểu "tail -f"): đang ở sát đáy thì theo chữ mới; người dùng cuộn lên đọc thì để yên. */
export const STICK_THRESHOLD = 80;

export function isNearBottom(scrollTop: number, clientHeight: number, scrollHeight: number, threshold = STICK_THRESHOLD): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= threshold;
}
