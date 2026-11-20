import SwiftUI

/// Chế độ giọng nói kiểu Siri (học từ barista_bds): khối tối bo tròn, quả cầu theo mức tiếng, viền chạy màu, phụ đề, các bước trợ lý đang làm,
/// gõ thay nói, chip gợi ý. Thay ô nhập chat khi đang nói; thẻ / câu trả lời vẫn hiện ở khung chat và khung làm việc phía trên.
struct SiriPill: View {
    @Environment(Desk.self) private var desk
    @State private var typing = false
    @State private var text = ""
    @FocusState private var focused: Bool

    private var mood: Orb.Mood { desk.voice.state == .thinking || desk.voice.state == .connecting ? .thinking : .idle }

    var body: some View {
        let v = desk.voice
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 14) {
                Button {
                    if v.state == .speaking { v.interrupt() } else if v.state == .off { desk.toggleVoice() }
                } label: {
                    SiriOrb(mood: mood, level: v.level)
                }
                .buttonStyle(.plain)
                .help(v.state == .speaking ? "Chạm để ngắt lời" : "")

                VStack(alignment: .leading, spacing: 4) {
                    if !v.userCaption.isEmpty {
                        Text(v.userCaption).font(.t14).foregroundStyle(.white.opacity(0.72)).lineLimit(2)
                    }
                    Text(v.botCaption.isEmpty ? stateText : v.botCaption).font(.t16).foregroundStyle(.white).lineLimit(4)
                    if !v.botCaption.isEmpty { Text(stateText).font(.t12).foregroundStyle(.white.opacity(0.72)) }
                    PillSteps()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)

                VStack(spacing: 10) {
                    Button { desk.stopVoiceMode() } label: { Image(systemName: "xmark").frame(width: 28, height: 28) }
                        .help("Tắt — về khung chat (Esc)")
                    Button { v.togglePause() } label: {
                        Image(systemName: v.paused ? "mic.slash.fill" : "mic.fill")
                            .foregroundStyle(v.paused ? Color.warn : .white.opacity(0.75))
                            .contentTransition(.symbolEffect(.replace))
                            .frame(width: 28, height: 28)
                    }
                    .help(v.paused ? "Bật micro" : "Tắt micro — trợ lý không nghe tiếng bên ngoài, vẫn nói tiếp được")
                    Button {
                        withAnimation(.snappy) { typing.toggle() }
                        focused = typing
                    } label: { Image(systemName: "keyboard").frame(width: 28, height: 28) }
                        .help("Gõ thay vì nói")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.white.opacity(0.75))
            }
            if v.canSendText && !typing && !chips.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        ForEach(chips, id: \.self) { chip in
                            Button { v.say(chip) } label: {
                                Text(chip).font(.t12m).lineLimit(1).foregroundStyle(.white.opacity(0.9))
                                    .padding(.horizontal, 10).padding(.vertical, 6)
                                    .background(Capsule().fill(.white.opacity(0.12)))
                                    .overlay(Capsule().strokeBorder(.white.opacity(0.18), lineWidth: 0.8))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .scrollIndicators(.never)
            }
            if typing {
                HStack(spacing: 6) {
                    TextField("Gõ cho trợ lý…", text: $text)
                        .textFieldStyle(.plain).font(.t14).foregroundStyle(.white)
                        .focused($focused)
                        .onSubmit { sendText() }
                    if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Button { sendText() } label: {
                            Image(systemName: "arrow.up").font(.system(size: 16, weight: .semibold)).foregroundStyle(.black)
                                .frame(width: 32, height: 32).background(Circle().fill(.white))
                        }
                        .buttonStyle(.plain).disabled(!v.canSendText).opacity(v.canSendText ? 1 : 0.4)
                    }
                }
                .padding(.leading, 12).padding(.trailing, 4).padding(.vertical, 4)
                .frame(minHeight: 40)
                .background(Capsule().fill(.white.opacity(0.1)))
                .transition(.move(edge: .top).combined(with: .opacity))
            }
            if let p = v.problem {
                Text(p).font(.t12).foregroundStyle(Color.warn).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 30, style: .continuous)
                .fill(Color(white: 0.07).opacity(0.9))   // đặc hơn: trên nền sáng, lớp đen mỏng ra xám đục làm chữ phụ đề chìm
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 30, style: .continuous))
        )
        .clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
        .overlay(RunningBorder(mood: mood, level: CGFloat(v.level), cornerRadius: 30))
        .environment(\.colorScheme, .dark)
        #if os(macOS)
        .onExitCommand { desk.stopVoiceMode() }
        #endif
    }

    private func sendText() {
        if desk.voice.say(text) {
            text = ""
            focused = false
        }
    }

    /// Gợi ý bấm nhanh = chính các câu hỏi mẫu theo vai của người đang dùng.
    private var chips: [String] { Array(desk.suggestions.prefix(4)) }

    private var stateText: String {
        let v = desk.voice
        switch v.state {
        case .off: return v.problem == nil ? "Đã tắt — chạm quả cầu để nói tiếp" : "Giọng nói chưa chạy"
        case .connecting: return "Đang kết nối…"
        case .listening: return v.paused ? "Đã tắt micro — bấm micro để bật lại" : "Em nghe đây…"
        case .thinking: return desk.live?.steps.last(where: { $0.done == nil })?.title ?? desk.live?.status ?? "Đang hỏi trợ lý…"
        case .speaking: return "Đang trả lời · chạm quả cầu để ngắt"
        }
    }
}

/// Các bước trợ lý đang làm (đang chạy: vòng quay; xong: dấu tick) — gọn, tối đa 3 dòng cuối.
private struct PillSteps: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        if let live = desk.live, !live.steps.isEmpty {
            VStack(alignment: .leading, spacing: 5) {
                ForEach(live.steps.suffix(3)) { s in
                    HStack(alignment: .top, spacing: 6) {
                        Group {
                            if s.done == nil {
                                ProgressView().controlSize(.mini)
                            } else {
                                Image(systemName: s.done == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                    .foregroundStyle(s.done == true ? Color(red: 0.3, green: 0.85, blue: 0.55) : Color.warn)
                            }
                        }
                        .frame(width: 14, height: 14)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.title).foregroundStyle(.white.opacity(0.95)).lineLimit(1)
                            if let sum = s.summary, !sum.isEmpty { Text(sum).foregroundStyle(.white.opacity(0.7)).lineLimit(1) }
                        }
                    }
                    .font(.t12)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            .padding(.top, 4)
            .animation(.spring(duration: 0.35), value: live.steps.count)
        }
    }
}

/// Tách đọc mức tiếng ra khỏi SiriPill: mỗi nhịp âm lượng chỉ vẽ lại quả cầu, không tính lại bố cục chữ / nút.
private struct SiriOrb: View {
    let mood: Orb.Mood
    let level: Float

    var body: some View {
        let halo = mood == .idle ? Color(red: 0.25, green: 0.45, blue: 0.9).opacity(0.5) : Color(red: 0.2, green: 0.55, blue: 1)
        ZStack {
            Circle()
                .fill(RadialGradient(colors: [halo.opacity(0.65), .clear], center: .center, startRadius: 4, endRadius: 45))
                .frame(width: 90, height: 90)
                .scaleEffect(1 + CGFloat(level) * 0.5)
            Orb(mood: mood, size: 58, level: level, glow: true)
        }
        .frame(width: 64, height: 64)
    }
}

/// Viền chạy màu kiểu Siri: một nét sắc đổi màu, nhịp theo âm lượng, không loang ra ngoài khối.
struct RunningBorder: View {
    var mood: Orb.Mood
    var level: CGFloat
    var cornerRadius: CGFloat

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 24, paused: mood == .idle && level == 0)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            let g = AngularGradient(colors: Self.colors + [Self.colors[0]], center: .center, angle: .degrees(t * (mood == .thinking ? 120 : 60)))
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .strokeBorder(g, lineWidth: 1.5 + level * 1.5)
                .opacity(mood == .idle ? 0.5 : 1)
        }
        .allowsHitTesting(false)
    }

    private static let colors = [Color(red: 0.25, green: 0.55, blue: 1), Color(red: 0.6, green: 0.35, blue: 1),
                                 Color(red: 1, green: 0.4, blue: 0.65), Color(red: 0.2, green: 0.85, blue: 0.9)]
}

/// Nút vào chế độ giọng nói: vòng chuyển màu xanh → tím → đỏ + biểu tượng sóng âm (như barista_bds).
struct SiriButton: View {
    var body: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: [.cold, Color(red: 0.6, green: 0.4, blue: 0.95), .bad], startPoint: .topLeading, endPoint: .bottomTrailing))
            Image(systemName: "waveform").font(.system(size: 16, weight: .bold)).foregroundStyle(.white)
        }
        .frame(width: 36, height: 36)
    }
}
