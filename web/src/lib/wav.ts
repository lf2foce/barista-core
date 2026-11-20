// Ghi chú hiện trường (0074 D): trình duyệt ghi âm thô (Float32, tần số của máy) ⇒ gộp, hạ về 16 kHz một kênh, đóng WAV 16-bit — định dạng
// model nghe tốt và gọn (~32 KB/giây). Hàm thuần để test; phần micro ở components/desk/dictate-mic.tsx.

export const NOTE_RATE = 16_000;

/** Gộp các khối mẫu rồi hạ tần số (lấy trung bình từng đoạn — đủ cho giọng nói), trả PCM 16-bit. */
export function toPcm16(chunks: Float32Array[], rate: number, target = NOTE_RATE): Int16Array {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const all = new Float32Array(total);
  let at = 0;
  for (const c of chunks) {
    all.set(c, at);
    at += c.length;
  }
  const step = rate / target;
  const out = new Int16Array(Math.floor(total / step));
  for (let i = 0; i < out.length; i++) {
    const from = Math.floor(i * step);
    const to = Math.min(total, Math.floor((i + 1) * step)) || from + 1;
    let sum = 0;
    for (let j = from; j < to; j++) sum += all[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, to - from)));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

/** WAV (RIFF, PCM 16-bit, một kênh). */
export function wavBytes(pcm: Int16Array, rate = NOTE_RATE): ArrayBuffer {
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + pcm.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // một kênh
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, pcm.length * 2, true);
  pcm.forEach((s, i) => v.setInt16(44 + i * 2, s, true));
  return buf;
}
