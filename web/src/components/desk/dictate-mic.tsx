"use client";

// ĐỌC CHÍNH TẢ (mic trong ô nhập — kiểu ChatGPT): bấm ghi — đang ghi hiện đồng hồ đỏ — bấm lần nữa ⇒ server CHỈ chép lời ⇒ chữ chèn vào ô nhập để
// người xem / sửa rồi TỰ gửi (không gửi luôn). Âm thô từ micro (Web Audio) ⇒ WAV 16 kHz (lib/wav.ts); âm thanh chỉ trong bộ nhớ, không lưu.
import { Loader2, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toPcm16, wavBytes } from "@/lib/wav";
import { useDesk } from "./desk-context";

type Rec = { ctx: AudioContext; stream: MediaStream; node: ScriptProcessorNode; chunks: Float32Array[]; started: number };

export function DictateMic({ onText, disabled }: { onText: (text: string) => void; disabled?: boolean }) {
  const desk = useDesk();
  const rec = useRef<Rec | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (startedAt === null) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [startedAt]);
  useEffect(() => () => void stop(), []);

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const ctx = new AudioContext();
      const node = ctx.createScriptProcessor(4096, 1, 1);
      const chunks: Float32Array[] = [];
      node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      ctx.createMediaStreamSource(stream).connect(node);
      node.connect(ctx.destination);
      rec.current = { ctx, stream, node, chunks, started: Date.now() };
      setStartedAt(Date.now());
      setNow(Date.now());
    } catch {
      desk.show("Cần cho phép trình duyệt dùng micro để ghi chú", true);
    }
  }

  async function stop(): Promise<ArrayBuffer | null> {
    const r = rec.current;
    rec.current = null;
    setStartedAt(null);
    if (!r) return null;
    r.node.disconnect();
    r.stream.getTracks().forEach((t) => t.stop());
    const rate = r.ctx.sampleRate;
    await r.ctx.close();
    if (Date.now() - r.started < 1000) return null;
    return wavBytes(toPcm16(r.chunks, rate));
  }

  async function toggle() {
    if (!rec.current) return start();
    const audio = await stop();
    if (!audio) return desk.show("Ghi âm quá ngắn — giữ lâu hơn một chút", true);
    setBusy(true);
    try {
      const text = (await api.transcribe(audio)).trim();
      if (text) onText(text);
      else desk.show("Em không nghe rõ — nói lại giúp em nhé", true);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không chép được lời", true);
    } finally {
      setBusy(false);
    }
  }

  const recording = startedAt !== null;
  const s = recording ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  if (recording) {
    return (
      <button type="button" onClick={() => void toggle()} className="t12m flex h-9 shrink-0 items-center gap-1.5 rounded-full bg-bad px-3 text-white outline-none focus-visible:ring-2 focus-visible:ring-ring tnum" aria-label="Dừng ghi — chữ sẽ hiện vào ô" title="Bấm để dừng — chữ sẽ hiện vào ô">
        <Square className="size-2.5 fill-current" aria-hidden />
        {Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}
      </button>
    );
  }
  return (
    <button type="button" onClick={() => void toggle()} disabled={disabled || busy}
            className="grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground outline-none hover:bg-foreground/[0.08] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
            aria-label="Đọc chính tả" title="Đọc chính tả — nói, chữ hiện vào ô để bạn sửa rồi gửi">
      {busy ? <Loader2 className="size-[18px] animate-spin" /> : <Mic className="size-[18px]" />}
    </button>
  );
}
