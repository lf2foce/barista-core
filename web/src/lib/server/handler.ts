import { cookies, headers } from "next/headers";
import { sameOrigin } from "./bff";
import { COOKIE, cookieOptions, isHttps, seal, unseal, type Session, SESSION_MAX_AGE_S } from "./session";

export async function readSession(): Promise<Session | null> {
  return unseal((await cookies()).get(COOKIE)?.value);
}

export async function writeSession(session: Session, request: Request): Promise<void> {
  const secure = isHttps(request.url, request.headers.get("x-forwarded-proto"));
  const maxAge = Math.max(60, Math.min(SESSION_MAX_AGE_S, Math.floor(session.exp - Date.now() / 1000)));
  (await cookies()).set(COOKIE, await seal(session), cookieOptions(secure, maxAge));
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(COOKIE);
}

export async function originOk(request: Request): Promise<boolean> {
  const h = await headers();
  return sameOrigin(request.method, h.get("origin"), h.get("x-forwarded-host") ?? h.get("host"));
}

export const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

export const detail = (message: string, status: number): Response => json({ detail: message }, status);
