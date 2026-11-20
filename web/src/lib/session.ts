/**
 * Rời phiên hỏng (hết hạn / không còn là thành viên): XOÁ phiên ở BFF trước, CHỈ khi xoá được mới về trang đăng nhập. Xoá không được (cookie còn đó)
 * mà vẫn điều hướng ⇒ trang đăng nhập thấy còn phiên, đẩy lại vào app, app lại lỗi — vòng lặp. Khi đó trả false để màn hiện "Đăng nhập lại".
 */
export async function leaveSession(logout: () => Promise<unknown>, go: (path: string) => void): Promise<boolean> {
  try {
    await logout();
  } catch {
    return false;
  }
  go("/login");
  return true;
}
