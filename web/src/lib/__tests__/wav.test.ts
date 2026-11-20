import { describe, expect, it } from "vitest";
import { toPcm16, wavBytes } from "../wav";

describe("ghi chú hiện trường — đóng WAV (0074 D)", () => {
  it("gộp khối, hạ 48 kHz về 16 kHz, kẹp biên độ", () => {
    const pcm = toPcm16([new Float32Array(48_000).fill(0.5), new Float32Array(48_000).fill(2)], 48_000);
    expect(pcm.length).toBe(32_000); // 2 giây ⇒ 2 × 16 000 mẫu
    expect(pcm[0]).toBe(Math.floor(0.5 * 0x7fff));
    expect(pcm[pcm.length - 1]).toBe(0x7fff); // 2 ⇒ kẹp về 1
  });
  it("đầu WAV đúng chuẩn RIFF / PCM / 16 kHz / một kênh", () => {
    const b = wavBytes(new Int16Array(10));
    const v = new DataView(b);
    const text = (o: number, n: number) => String.fromCharCode(...new Uint8Array(b, o, n));
    expect([text(0, 4), text(8, 4), text(36, 4)]).toEqual(["RIFF", "WAVE", "data"]);
    expect([v.getUint16(20, true), v.getUint16(22, true), v.getUint32(24, true), v.getUint32(40, true)]).toEqual([1, 1, 16_000, 20]);
    expect(b.byteLength).toBe(64);
  });
});
