import SwiftUI
#if os(macOS)
import AppKit
import Carbon.HIToolbox

/// Chế độ Siri nổi trên Mac (học từ barista_bds): bấm quả cầu ⇒ cửa sổ chính lui đi, khối Siri nổi ở góc màn hình (trên mọi Space,
/// không che app đang dùng), thứ trợ lý mở ra trượt vào NGĂN THẺ nổi ở cạnh phải — mỗi thẻ một tab. Đóng (✕ / Esc) ⇒ về cửa sổ chính.
/// iPhone không dùng: khối Siri nằm ngay trong cột chat (Siri.swift).

/// Cửa sổ nổi không viền: trôi trên mọi Space, gõ được chữ, không giành focus của app đang dùng khi chưa bấm vào.
final class FloatPanel: NSPanel {
    init<V: View>(size: NSSize, @ViewBuilder content: () -> V) {
        super.init(contentRect: NSRect(origin: .zero, size: size),
                   styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView], backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovableByWindowBackground = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        setContent(content())
    }

    func setContent<V: View>(_ view: V) {
        let host = NSHostingView(rootView: view)
        host.sizingOptions = []
        contentView = host
    }

    override var canBecomeKey: Bool { true }

    /// .bottomLeading ⇒ khối Siri góc dưới trái; .trailing ⇒ ngăn thẻ dọc cạnh phải.
    func place(_ corner: Alignment) {
        guard let f = NSScreen.main?.visibleFrame else { return }
        if corner == .trailing {
            setFrame(NSRect(x: f.maxX - frame.width - 8, y: f.minY + 8, width: frame.width, height: f.height - 16), display: true)
        } else {
            setFrameOrigin(NSPoint(x: f.minX + 16, y: f.minY + 8))
        }
    }
}

/// Điều phối hai cửa sổ nổi + cửa sổ chính. Một bản cho cả app; gắn lại khi đổi người dùng (Desk mới).
@MainActor
final class VoiceWindows {
    static let shared = VoiceWindows()
    private var siri: FloatPanel?
    private var drawer: FloatPanel?
    private weak var desk: Desk?
    private weak var main: NSWindow?

    private var hotKey: HotKey?
    var active: Bool { desk?.voiceMode ?? false }

    func bind(_ desk: Desk) {
        self.desk = desk
        // ⌥⌘V ở bất kỳ đâu (kể cả đang dùng app khác): bật / tắt chế độ Siri — như barista_bds
        hotKey = hotKey ?? HotKey(id: 1, key: kVK_ANSI_V) { [weak self] in
            guard let d = self?.desk else { return }
            if d.voiceMode { d.stopVoiceMode() } else { d.startVoiceMode() }
        }
        let pill = FloatPanel(size: NSSize(width: 560, height: 420)) { FloatingPill().environment(desk) }
        let side = FloatPanel(size: NSSize(width: drawerWidth, height: 800)) {
            VoiceDrawer(resize: { [weak self] x in self?.resizeDrawer(toMouseX: x) },
                        reset: { [weak self] in self?.resetDrawer() }).environment(desk)
        }
        side.isMovableByWindowBackground = false
        siri?.orderOut(nil)
        drawer?.orderOut(nil)
        siri = pill
        drawer = side
        desk.present = { [weak self] on in self?.present(on) }
        watch()
    }

    // ── Ngăn thẻ rộng / hẹp: mặc định ~55% màn hình (tối đa 960), kéo mép trái để đổi, nhớ cho lần sau ──
    private static let widthKey = "voiceDrawerWidth"
    private var screen: NSRect { NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900) }
    private var defaultWidth: CGFloat { min(960, max(640, screen.width * 0.55)) }
    private var maxWidth: CGFloat { max(480, screen.width - 500) }   // chừa chỗ cho khối Siri bên trái
    private var drawerWidth: CGFloat {
        let saved = UserDefaults.standard.double(forKey: Self.widthKey)
        return min(maxWidth, saved > 0 ? saved : defaultWidth)
    }

    /// Theo vị trí chuột trên MÀN HÌNH (không theo toạ độ trong cửa sổ — cửa sổ đang co giãn dưới tay kéo).
    private func resizeDrawer(toMouseX x: CGFloat) {
        let w = min(maxWidth, max(480, screen.maxX - 8 - x))
        UserDefaults.standard.set(Double(w), forKey: Self.widthKey)
        drawer?.setFrame(NSRect(x: screen.maxX - w - 8, y: screen.minY + 8, width: w, height: screen.height - 16), display: true)
    }

    private func resetDrawer() {
        UserDefaults.standard.removeObject(forKey: Self.widthKey)
        resizeDrawer(toMouseX: screen.maxX - 8 - defaultWidth)
        UserDefaults.standard.removeObject(forKey: Self.widthKey)
    }

    private func present(_ on: Bool) {
        if on {
            main = NSApp.windows.first { !($0 is NSPanel) && $0.isVisible } ?? main
            main?.orderOut(nil)
            siri?.place(.bottomLeading)
            drawer?.setContentSize(NSSize(width: drawerWidth, height: 800))
            drawer?.place(.trailing)
            siri?.makeKeyAndOrderFront(nil)
        } else {
            siri?.orderOut(nil)
            main?.makeKeyAndOrderFront(nil)
            NSApp.activate()
        }
        syncDrawer()
    }

    /// Ngăn thẻ là cửa sổ trong suốt: chỉ đưa ra khi chế độ Siri CÓ thẻ, để không che chuột app bên dưới.
    private func watch() {
        withObservationTracking {
            _ = desk?.voiceMode
            _ = desk?.voiceOpened.isEmpty
        } onChange: {
            DispatchQueue.main.async { [weak self] in
                self?.syncDrawer()
                self?.watch()
            }
        }
    }

    private func syncDrawer() {
        guard let desk, let drawer else { return }
        if desk.voiceMode && !desk.voiceOpened.isEmpty {
            drawer.orderFrontRegardless()
        } else if drawer.isVisible {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in   // chờ hiệu ứng trượt ra xong rồi mới cất
                guard let self, let d = self.desk, !d.voiceMode || d.voiceOpened.isEmpty else { return }
                self.drawer?.orderOut(nil)
            }
        }
    }
}

/// Phím tắt toàn hệ thống ⌥⌘<phím> (Carbon — không cần quyền Trợ năng).
final class HotKey {
    nonisolated(unsafe) private static var actions: [UInt32: () -> Void] = [:]
    nonisolated(unsafe) private static var installed = false
    private var ref: EventHotKeyRef?

    init(id: UInt32, key: Int, action: @escaping () -> Void) {
        HotKey.actions[id] = action
        if !HotKey.installed {
            HotKey.installed = true
            var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
            InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
                var hk = EventHotKeyID()
                GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil,
                                  MemoryLayout<EventHotKeyID>.size, nil, &hk)
                let id = hk.id
                DispatchQueue.main.async { HotKey.actions[id]?() }
                return noErr
            }, 1, &spec, nil, nil)
        }
        let hid = EventHotKeyID(signature: OSType(0x4252_5354), id: id)   // "BRST"
        RegisterEventHotKey(UInt32(key), UInt32(cmdKey | optionKey), hid, GetApplicationEventTarget(), 0, &ref)
    }

    deinit { if let ref { UnregisterEventHotKey(ref) } }
}

/// Khối Siri đặt trong cửa sổ nổi: bề ngang cố định, bám góc dưới trái.
struct FloatingPill: View {
    var body: some View {
        SiriPill()
            .frame(width: 440)
            .fixedSize(horizontal: false, vertical: true)
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
    }
}

/// Ngăn thẻ cạnh phải: thứ trợ lý mở ra trong lúc nói chuyện, mỗi thứ một tab; thẻ y như ở khung làm việc (bấm nút, mở chi tiết được).
struct VoiceDrawer: View {
    @Environment(Desk.self) private var desk
    var resize: (CGFloat) -> Void = { _ in }
    var reset: () -> Void = {}
    @State private var gripHover = false

    private var items: [CanvasItem] { desk.voiceOpened.compactMap { id in desk.canvas.first { $0.id == id } } }

    var body: some View {
        ZStack(alignment: .trailing) {
            if let hero = items.first(where: { $0.id == desk.activeId }) ?? items.last {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 6) {
                        ScrollView(.horizontal) {
                            HStack(spacing: 6) { ForEach(items) { tab($0, selected: $0.id == hero.id) } }
                        }
                        .scrollIndicators(.never)
                        Button { withAnimation(.snappy) { desk.voiceOpened = [] } } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.borderless).foregroundStyle(.secondary).help("Đóng ngăn thẻ")
                    }
                    CardFrame(item: hero)
                        .id(hero.id)
                        .transition(.push(from: .trailing))
                }
                .padding(14)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                .overlay(alignment: .leading) { grip }
                .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color.canvas))
                .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
                .compositingGroup()
                .shadow(color: .black.opacity(0.18), radius: 14, y: 6)
                .padding(16)
                .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .trailing)
        .animation(.spring(response: 0.55, dampingFraction: 0.85), value: desk.activeId)
        .animation(.spring(response: 0.55, dampingFraction: 0.85), value: desk.voiceOpened.isEmpty)
    }

    /// Tay nắm mép trái: kéo để rộng / hẹp ngăn thẻ; bấm đúp về độ rộng mặc định.
    private var grip: some View {
        Capsule()
            .fill(Color.primary.opacity(gripHover ? 0.35 : 0.15))
            .frame(width: 4, height: 44)
            .frame(width: 14)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .onHover { inside in
                gripHover = inside
                if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
            }
            .gesture(DragGesture(minimumDistance: 1).onChanged { _ in resize(NSEvent.mouseLocation.x) })
            .onTapGesture(count: 2) { reset() }
            .help("Kéo để rộng / hẹp · bấm đúp: về mặc định")
            .offset(x: -7)
    }

    private func tab(_ item: CanvasItem, selected: Bool) -> some View {
        Button { withAnimation(.snappy) { desk.activeId = item.id } } label: {
            HStack(spacing: 6) {
                Image(systemName: item.style.symbol).font(.system(size: 11, weight: .semibold)).foregroundStyle(item.style.tint)
                Text(item.title).lineLimit(1).frame(maxWidth: 200, alignment: .leading)
            }
            .font(.t12m)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Capsule().fill(selected ? Color.surface : Color.primary.opacity(0.05)))
            .foregroundStyle(selected ? Color.primary : Color.secondary)
        }
        .buttonStyle(.plain)
        .contextMenu { Button("Đóng tab") { desk.voiceOpened.removeAll { $0 == item.id } } }
    }
}
/// `--snapshot-voice <thư mục>`: nghiệm thu giao diện chế độ Siri nổi mà không chen vào màn hình người dùng. Vai / thẻ lấy từ
/// BARISTA_EMAIL (mặc định truonga@…), BARISTA_TENANT, BARISTA_OPEN ('truy_vấn {json}'), BARISTA_SAY_CAPTION (phụ đề giả).
@MainActor
enum VoiceSnapshot {
    static func run(into dir: URL) async {
        let env = ProcessInfo.processInfo.environment
        let tenant = env["BARISTA_TENANT"] ?? ""
        let desk = Desk(api: API(base: API.defaultBase, email: env["BARISTA_EMAIL"] ?? "truonga@sanmau.demo", tenant: tenant))
        await desk.load()
        desk.voiceMode = true
        if let raw = env["BARISTA_OPEN"], let sp = raw.firstIndex(of: " ") {
            let input = (try? JSONDecoder().decode(Input.self, from: Data(raw[sp...].utf8))) ?? [:]
            await desk.openTarget(query: String(raw[..<sp]), input: input, title: "")
        }
        desk.voice.userCaption = env["BARISTA_SAY_CAPTION"] ?? "Khách Lan dự án Hồ Sen đang thế nào?"
        desk.voice.botCaption = "Chị Lan đang hỏi chiết khấu, An đã nhắc anh duyệt trong trao đổi. Em mở thẻ khách bên phải để anh xem."
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        await render(FloatingPill().environment(desk), size: NSSize(width: 560, height: 360), to: dir.appending(path: "voice-pill.png"))
        await render(VoiceDrawer().environment(desk), size: NSSize(width: 900, height: Double(env["BARISTA_SNAPSHOT_HEIGHT"] ?? "") ?? 900), to: dir.appending(path: "voice-drawer.png"))
        print("ok:", dir.path, "· thẻ:", desk.voiceOpened)
    }

    static func render<V: View>(_ view: V, size: NSSize, to url: URL, settle: TimeInterval = 0.6) async {
        let win = NSWindow(contentRect: NSRect(origin: NSPoint(x: -20000, y: -20000), size: size), styleMask: [.borderless],
                           backing: .buffered, defer: false)
        win.backgroundColor = NSColor(white: 0.5, alpha: 1)   // nền xám để thấy rõ mép khối trong suốt
        let host = NSHostingView(rootView: view)
        host.frame = NSRect(origin: .zero, size: size)
        win.contentView = host
        if settle > 1 {
            // cửa sổ phải "hiện" thì .task / onAppear mới chạy (dữ liệu tải trong view) — đặt ngoài màn hình + trong suốt hẳn: người dùng không thấy
            win.alphaValue = 0
            win.ignoresMouseEvents = true
            win.orderFrontRegardless()
        }
        host.layoutSubtreeIfNeeded()
        // NHƯỜNG luồng chính trong lúc chờ (RunLoop.run giữ luồng ⇒ .task của view không chạy được) — SwiftUI dựng xong, dữ liệu tải xong
        try? await Task.sleep(for: .seconds(settle))
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        host.cacheDisplay(in: host.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}
#endif
