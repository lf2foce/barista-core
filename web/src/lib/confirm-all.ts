import type { DecideOutcome } from "./decide";
import type { ChatCard, Input } from "./types";

// "Xác nhận cả N đề xuất" (0050, ChatView.swift confirmAll): kể lại buổi gặp thường ra nhiều thẻ trong MỘT lượt ⇒ một nút xác nhận hết, lần
// lượt từng thẻ (khoá chống lặp mới mỗi thẻ). Lỗi đầu tiên ⇒ DỪNG, mở thẻ đó cho người sửa. Thẻ cuối được quyết ⇒ server chạy tiếp.

export function waitingProposals(cards: ChatCard[], decided: Record<string, unknown>): ChatCard[] {
  return cards.filter((c) => c.kind === "proposal" && c.resumable === true && !c.aside && decided[c.id] === undefined && (c.state ?? "proposed") === "proposed");
}

export type ConfirmAllResult = { confirmed: { card: ChatCard; outcome: DecideOutcome }[]; failed: { card: ChatCard; outcome: DecideOutcome } | null };

export async function confirmAll(
  cards: ChatCard[],
  decide: (card: ChatCard, input: Input, key: string) => Promise<DecideOutcome>,
  inputOf: (card: ChatCard) => Input,
  newKey: () => string,
  onConfirmed?: (card: ChatCard, outcome: DecideOutcome) => void,
): Promise<ConfirmAllResult> {
  const confirmed: ConfirmAllResult["confirmed"] = [];
  for (const card of cards) {
    const outcome = await decide(card, inputOf(card), newKey());
    if (!outcome.done) return { confirmed, failed: { card, outcome } };
    confirmed.push({ card, outcome });
    onConfirmed?.(card, outcome);
  }
  return { confirmed, failed: null };
}
