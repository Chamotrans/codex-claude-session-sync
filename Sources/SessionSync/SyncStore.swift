import Foundation
import SwiftUI
import Combine

@MainActor
final class SyncStore: ObservableObject {
    @Published var scan: ScanResult?
    @Published var isBusy = false
    @Published var busyText = ""
    @Published var lastRefresh: Date?
    @Published var lastError: String?
    @Published var log: [LogEntry] = []
    @Published var filter: RowFilter = .all
    @Published var search = ""
    @Published var selection: SyncRow.ID?
    @Published var pendingConflict: PairInfo?

    @AppStorage("autoSync") var autoSync = false
    @AppStorage("autoCreate") var autoCreate = false
    @AppStorage("refreshInterval") var refreshInterval: Double = 60

    private var timer: AnyCancellable?
    private var watchers: [DirectoryWatcher] = []
    private var pendingRefresh: DispatchWorkItem?

    struct LogEntry: Identifiable {
        let id = UUID()
        let date = Date()
        let text: String
        let isError: Bool
    }

    init() {
        startTimer()
        startWatchers()
    }

    // MARK: rows

    var rows: [SyncRow] {
        guard let scan = scan else { return [] }
        var all: [SyncRow] = scan.pairs.map { .pair($0) }
        all += scan.unpaired.claude.map { .only($0) }
        all += scan.unpaired.codex.map { .only($0) }
        all = all.filter { row in
            switch filter {
            case .all: return true
            case .needsSync:
                if case .pair(let p) = row { return p.status.needsAction }
                return true
            case .conflicts:
                if case .pair(let p) = row { return p.status == .conflict }
                return false
            case .onlyClaude:
                if case .only(let s) = row { return s.side == "claude" }
                return false
            case .onlyCodex:
                if case .only(let s) = row { return s.side == "codex" }
                return false
            case .inSync:
                if case .pair(let p) = row { return p.status == .inSync }
                return false
            }
        }
        if !search.isEmpty {
            let q = search.lowercased()
            all = all.filter { $0.title.lowercased().contains(q) || $0.projectName.lowercased().contains(q) }
        }
        return all.sorted { ($0.newestDate ?? .distantPast) > ($1.newestDate ?? .distantPast) }
    }

    func count(for f: RowFilter) -> Int {
        guard let scan = scan else { return 0 }
        switch f {
        case .all: return scan.pairs.count + scan.unpaired.claude.count + scan.unpaired.codex.count
        case .needsSync: return scan.pairs.filter { $0.status.needsAction }.count + scan.unpaired.claude.count + scan.unpaired.codex.count
        case .conflicts: return scan.pairs.filter { $0.status == .conflict }.count
        case .onlyClaude: return scan.unpaired.claude.count
        case .onlyCodex: return scan.unpaired.codex.count
        case .inSync: return scan.pairs.filter { $0.status == .inSync }.count
        }
    }

    var selectedRow: SyncRow? { rows.first { $0.id == selection } }

    // MARK: actions

    private func perform(_ text: String, _ body: @escaping () async throws -> String?) {
        guard !isBusy else { return }
        isBusy = true
        busyText = text
        lastError = nil
        Task {
            do {
                if let msg = try await body() { addLog(msg) }
            } catch {
                lastError = error.localizedDescription
                addLog(error.localizedDescription, isError: true)
            }
            isBusy = false
            busyText = ""
        }
    }

    func refresh(bootstrap: Bool = false) {
        perform(bootstrap ? "正在配對…" : "正在掃描…") { [self] in
            let result = bootstrap ? try await Engine.shared.bootstrap() : try await Engine.shared.scan()
            scan = result
            lastRefresh = Date()
            if autoSync { await autoSyncPass() }
            return nil
        }
    }

    func syncPair(_ pair: PairInfo, prefer: String? = nil) {
        if pair.status == .conflict && prefer == nil {
            pendingConflict = pair
            return
        }
        perform("同步中：\(pair.title)") { [self] in
            let r = try await Engine.shared.sync(pair: pair.pairId, prefer: prefer)
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            return describe(r, title: pair.title)
        }
    }

    func syncAll() {
        perform("同步全部…") { [self] in
            let r = try await Engine.shared.syncAll()
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            var msgs: [String] = []
            for x in r.results {
                if let e = x.error { msgs.append("\(x.pairId.prefix(8)): \(e)") } else { msgs.append(describe(x, title: String(x.pairId.prefix(8)))) }
            }
            return msgs.isEmpty ? "全部已同步，冇嘢要推。" : msgs.joined(separator: "\n")
        }
    }

    func create(from session: SessionInfo) {
        perform("建立副本：\(session.title)") { [self] in
            let r = try await Engine.shared.create(from: session.side, id: session.id)
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            return "已喺 \(r.side == "codex" ? "Codex" : "Claude") 建立副本：\(session.title)"
        }
    }

    func ignore(_ session: SessionInfo) {
        perform("隱藏…") { [self] in
            try await Engine.shared.ignore(side: session.side, id: session.id)
            scan = try await Engine.shared.scan()
            return "已隱藏：\(session.title)"
        }
    }

    func unlink(_ pair: PairInfo) {
        perform("解除配對…") { [self] in
            try await Engine.shared.unlink(pair: pair.pairId)
            scan = try await Engine.shared.scan()
            return "已解除配對：\(pair.title)"
        }
    }

    func link(claude: SessionInfo, codex: SessionInfo) {
        perform("配對中…") { [self] in
            try await Engine.shared.link(claude: claude.id, codex: codex.id)
            scan = try await Engine.shared.scan()
            return "已配對：\(claude.title) ↔ \(codex.title)"
        }
    }

    func registerInDesktop(_ session: SessionInfo) {
        perform("登記到 Claude Desktop…") { [self] in
            let r = try await Engine.shared.register(claudeId: session.id)
            scan = try await Engine.shared.scan()
            return (r.already ?? false) ? "\(session.title)：側欄已經有。" : "已登記到 Claude Desktop 側欄：\(session.title)（如未出現請重開 Claude app）"
        }
    }

    func registerAllInDesktop() {
        perform("登記全部到 Claude Desktop…") { [self] in
            let r = try await Engine.shared.registerAll()
            scan = try await Engine.shared.scan()
            return r.registered == 0 ? "所有 Claude 對話都已經喺側欄。" : "已登記 \(r.registered) 個對話到 Claude Desktop 側欄（如未出現請重開 Claude app）"
        }
    }

    var missingFromDesktopCount: Int {
        guard let scan = scan else { return 0 }
        let all = scan.pairs.compactMap { $0.claude } + scan.unpaired.claude
        // Only Codex-derived sessions count; the user's own CLI sessions can be registered one by one from the row menu.
        return all.filter { !$0.isInDesktopSidebar && ($0.imported ?? false) }.count
    }

    func copyResumeCommand(_ session: SessionInfo) {
        Task {
            do {
                let r = try await Engine.shared.openCommand(side: session.side, id: session.id)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(r.command, forType: .string)
                addLog("已複製指令：\(r.command)")
            } catch {
                addLog(error.localizedDescription, isError: true)
            }
        }
    }

    func revealInFinder(_ session: SessionInfo) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: session.path)])
    }

    private func autoSyncPass() async {
        guard let scan = scan else { return }
        let needs = scan.pairs.filter { $0.status == .claudeNewer || $0.status == .codexNewer }
        var changed = false
        if !needs.isEmpty {
            if let r = try? await Engine.shared.syncAll() {
                for x in r.results where x.error == nil { addLog("自動同步：" + describe(x, title: String(x.pairId.prefix(8)))); changed = true }
                for x in r.results where x.error != nil { addLog("自動同步略過 \(x.pairId.prefix(8))：\(x.error ?? "")", isError: false) }
            }
        }
        if autoCreate {
            for s in scan.unpaired.claude + scan.unpaired.codex where s.active != true {
                if let r = try? await Engine.shared.create(from: s.side, id: s.id) {
                    addLog("自動建立 \(r.side == "codex" ? "Codex" : "Claude") 副本：\(s.title)")
                    changed = true
                }
            }
        }
        if changed, let fresh = try? await Engine.shared.scan() { self.scan = fresh }
    }

    private func describe(_ r: SyncResult, title: String) -> String {
        let toCodex = r.pushedToCodex ?? 0, toClaude = r.pushedToClaude ?? 0
        if toCodex == 0 && toClaude == 0 { return "\(title)：已經係最新。" }
        var parts: [String] = []
        if toCodex > 0 { parts.append("Claude → Codex \(toCodex) 個回合") }
        if toClaude > 0 { parts.append("Codex → Claude \(toClaude) 個回合") }
        return "\(title)：" + parts.joined(separator: "，")
    }

    func addLog(_ text: String, isError: Bool = false) {
        log.insert(LogEntry(text: text, isError: isError), at: 0)
        if log.count > 200 { log.removeLast() }
    }

    // MARK: background

    func startTimer() {
        timer?.cancel()
        let interval = max(15, refreshInterval)
        timer = Timer.publish(every: interval, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            guard let self = self, !self.isBusy else { return }
            self.refresh()
        }
    }

    private func startWatchers() {
        let home = NSHomeDirectory()
        let paths = [home + "/.claude/projects", home + "/.codex/sessions", home + "/.codex"]
        watchers = paths.compactMap { DirectoryWatcher(path: $0) { [weak self] in self?.scheduleRefresh() } }
    }

    private func scheduleRefresh() {
        pendingRefresh?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, !self.isBusy else { return }
            self.refresh()
        }
        pendingRefresh = work
        // Debounce: agents write many lines per second while a turn is running.
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: work)
    }
}

/// Coalesced change notifications for a directory tree (FSEvents).
final class DirectoryWatcher {
    private var stream: FSEventStreamRef?
    private let callback: () -> Void

    init?(path: String, callback: @escaping () -> Void) {
        self.callback = callback
        guard FileManager.default.fileExists(atPath: path) else { return nil }
        var ctx = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        let cb: FSEventStreamCallback = { _, info, _, _, _, _ in
            guard let info = info else { return }
            let w = Unmanaged<DirectoryWatcher>.fromOpaque(info).takeUnretainedValue()
            DispatchQueue.main.async { w.callback() }
        }
        guard let s = FSEventStreamCreate(nil, cb, &ctx, [path] as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 3.0,
                                          UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagNoDefer)) else { return nil }
        stream = s
        FSEventStreamSetDispatchQueue(s, DispatchQueue.global(qos: .utility))
        FSEventStreamStart(s)
    }

    deinit {
        if let s = stream {
            FSEventStreamStop(s)
            FSEventStreamInvalidate(s)
            FSEventStreamRelease(s)
        }
    }
}
