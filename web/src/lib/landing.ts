/** Trang giới thiệu sản phẩm (bản cài khai ``"landing": true`` ⇒ BARISTA_WEB_HOME=landing): "/" của người CHƯA đăng nhập hiện trang này, nút
 *  "Đăng nhập" dẫn tới /login. Vắng ⇒ "/" về thẳng /login như trước. Đọc lúc chạy (proxy chạy Node), không đóng cứng lúc build — một ảnh cho mọi bản cài. */
export const LANDING = "/gioi-thieu";
export const landingOn = () => process.env.BARISTA_WEB_HOME === "landing";
