import AuthenticationServices
import CryptoKit
import Foundation
import Security
import SwiftUI

/// Đăng nhập thật (quyết định 0040): app lấy id_token Google bằng PKCE (không bí mật nào trong app), đưa cho core kiểm; core cấp phiên.
/// Phiên giữ trong Keychain. App không tự quyết ai được vào — core quyết (đã được mời chưa, có bị khoá không).

struct AuthConfig: Decodable {
    struct Google: Decodable { let client_id: String }
    let google: Google?
    let dev: Bool
}

struct TenantChoice: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let role_label: String
}

struct SessionGrant: Decodable {
    let token: String
    let email: String
    let tenants: [TenantChoice]
}

extension API {
    static func authConfig(base: URL = API.defaultBase) async throws -> AuthConfig {
        let (d, r) = try await URLSession.shared.data(from: base.appending(path: "/v1/auth/config"))
        guard (r as? HTTPURLResponse)?.statusCode == 200 else { throw APIError(message: "Không hỏi được cách đăng nhập") }
        return try JSONDecoder().decode(AuthConfig.self, from: d)
    }

    static func loginGoogle(idToken: String, nonce: String, base: URL = API.defaultBase) async throws -> SessionGrant {
        var req = URLRequest(url: base.appending(path: "/v1/auth/google"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["id_token": idToken, "nonce": nonce])
        let (d, r) = try await URLSession.shared.data(for: req)
        guard (r as? HTTPURLResponse)?.statusCode == 200 else {
            let detail = (try? JSONDecoder().decode([String: String].self, from: d))?["detail"]
            throw APIError(message: detail ?? "Đăng nhập không thành công")
        }
        return try JSONDecoder().decode(SessionGrant.self, from: d)
    }

    /// Thu hồi phiên ở core (máy khác của cùng người vẫn đăng nhập).
    func logout() async {
        var req = URLRequest(url: base.appending(path: "/v1/auth/logout"))
        req.httpMethod = "POST"
        identify(&req)
        _ = try? await URLSession.shared.data(for: req)
    }
}

/// Mã phiên trong Keychain (không để ở UserDefaults — file thường đọc được).
enum SessionStore {
    private static let service = "vn.barista.desk", account = "session"

    static func load() -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String) {
        delete()
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecValueData as String: Data(token.utf8),
                                kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        SecItemAdd(q as CFDictionary, nil)
    }

    static func delete() {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        SecItemDelete(q as CFDictionary)
    }
}

/// OAuth 2.0 + PKCE với Google trong cửa sổ đăng nhập của hệ thống (dùng luôn phiên Google trong Safari ⇒ thường một chạm).
enum GoogleSignIn {
    struct Result { let idToken: String; let nonce: String }

    static func signIn(clientID: String, session: WebAuthenticationSession) async throws -> Result {
        // client loại "iOS" của Google: lược đồ chuyển hướng = client ID viết ngược
        let scheme = clientID.split(separator: ".").reversed().joined(separator: ".")
        let redirect = "\(scheme):/oauthredirect"
        let verifier = random(32), state = random(16), nonce = random(16)
        let challenge = base64url(Data(SHA256.hash(data: Data(verifier.utf8))))
        var c = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth")!
        c.queryItems = [.init(name: "client_id", value: clientID), .init(name: "redirect_uri", value: redirect),
                        .init(name: "response_type", value: "code"), .init(name: "scope", value: "openid email profile"),
                        .init(name: "code_challenge", value: challenge), .init(name: "code_challenge_method", value: "S256"),
                        .init(name: "state", value: state), .init(name: "nonce", value: nonce),
                        .init(name: "prompt", value: "select_account")]
        let callback = try await session.authenticate(using: c.url!, callbackURLScheme: scheme)
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        guard items.first(where: { $0.name == "state" })?.value == state else { throw APIError(message: "Phiên đăng nhập không khớp — thử lại") }
        if let err = items.first(where: { $0.name == "error" })?.value { throw APIError(message: "Google từ chối: \(err)") }
        guard let code = items.first(where: { $0.name == "code" })?.value else { throw APIError(message: "Google không trả mã đăng nhập") }

        var req = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [.init(name: "client_id", value: clientID), .init(name: "code", value: code),
                           .init(name: "code_verifier", value: verifier), .init(name: "grant_type", value: "authorization_code"),
                           .init(name: "redirect_uri", value: redirect)]
        req.httpBody = form.percentEncodedQuery?.data(using: .utf8)
        let (d, r) = try await URLSession.shared.data(for: req)
        guard (r as? HTTPURLResponse)?.statusCode == 200,
              let obj = try JSONSerialization.jsonObject(with: d) as? [String: Any], let idToken = obj["id_token"] as? String
        else { throw APIError(message: "Không đổi được mã đăng nhập Google") }
        return Result(idToken: idToken, nonce: nonce)
    }

    private static func random(_ bytes: Int) -> String {
        var b = [UInt8](repeating: 0, count: bytes)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes, &b)
        return base64url(Data(b))
    }

    private static func base64url(_ d: Data) -> String {
        d.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
