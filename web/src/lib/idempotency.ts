// Khoá chống lặp: một khoá cho mỗi hộp thoại — bấm hai lần / gửi lại lúc mạng chập vẫn là MỘT lệnh ở core.
// Lệnh bị từ chối vì conflict / invalid thì người dùng sửa rồi gửi lại = yêu cầu mới ⇒ đổi khoá (FormSheet.swift).

export function newKey(): string {
  return globalThis.crypto.randomUUID();
}

export function nextKey(current: string, status: string): string {
  return status === "conflict" || status === "invalid" ? newKey() : current;
}
