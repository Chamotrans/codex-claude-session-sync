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
        if filter == .echoes { all = (scan.echoes ?? []).map { .only($0) } }
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
            case .echoes:
                return true
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
        case .echoes: return scan.echoes?.count ?? 0
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
        perform(bootstrap ? String(localized: "Pairing…") : String(localized: "Scanning…")) { [self] in
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
        perform(String(localized: "Syncing: \(pair.title)")) { [self] in
            let r = try await Engine.shared.sync(pair: pair.pairId, prefer: prefer)
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            return describe(r, title: pair.title)
        }
    }

    func syncAll() {
        perform(String(localized: "Syncing all…")) { [self] in
            let r = try await Engine.shared.syncAll()
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            var msgs: [String] = []
            for x in r.results {
                if let e = x.error { msgs.append("\(x.pairId.prefix(8)): \(e)") } else { msgs.append(describe(x, title: String(x.pairId.prefix(8)))) }
            }
            return msgs.isEmpty ? String(localized: "Everything is in sync; nothing to push.") : msgs.joined(separator: "\n")
        }
    }

    func create(from session: SessionInfo) {
        perform(String(localized: "Creating copy: \(session.title)")) { [self] in
            let r = try await Engine.shared.create(from: session.side, id: session.id)
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            return r.side == "codex" ? String(localized: "Created a Codex copy: \(session.title)") : String(localized: "Created a Claude copy: \(session.title)")
        }
    }

    func recreate(_ pair: PairInfo) {
        perform(String(localized: "Recreating the missing side…")) { [self] in
            let r = try await Engine.shared.recreate(pair: pair.pairId)
            scan = try await Engine.shared.scan()
            lastRefresh = Date()
            return r.recreated == "codex" ? String(localized: "Recreated in Codex: \(pair.title)") : String(localized: "Recreated in Claude: \(pair.title)")
        }
    }

    func ignore(_ session: SessionInfo) {
        perform(String(localized: "Hiding…")) { [self] in
            try await Engine.shared.ignore(side: session.side, id: session.id)
            scan = try await Engine.shared.scan()
            return String(localized: "Hidden: \(session.title)")
        }
    }

    func unlink(_ pair: PairInfo) {
        perform(String(localized: "Unlinking…")) { [self] in
            try await Engine.shared.unlink(pair: pair.pairId)
            scan = try await Engine.shared.scan()
            return String(localized: "Unlinked: \(pair.title)")
        }
    }

    func link(claude: SessionInfo, codex: SessionInfo) {
        perform(String(localized: "Linking…")) { [self] in
            try await Engine.shared.link(claude: claude.id, codex: codex.id)
            scan = try await Engine.shared.scan()
            return String(localized: "Linked: \(claude.title) ↔ \(codex.title)")
        }
    }

    func registerInDesktop(_ session: SessionInfo) {
        perform(String(localized: "Adding to the Claude desktop sidebar…")) { [self] in
            let r = try await Engine.shared.register(claudeId: session.id)
            scan = try await Engine.shared.scan()
            return (r.already ?? false) ? String(localized: "\(session.title): already in the sidebar.") : String(localized: "Added to the Claude desktop sidebar: \(session.title). Restart the Claude app if it does not appear.")
        }
    }

    func registerAllInDesktop() {
        perform(String(localized: "Adding all to the Claude desktop sidebar…")) { [self] in
            let r = try await Engine.shared.registerAll()
            scan = try await Engine.shared.scan()
            return r.registered == 0 ? String(localized: "All Claude conversations are already in the sidebar.") : String(localized: "Added \(r.registered) conversations to the Claude desktop sidebar. Restart the Claude app if they do not appear.")
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
                addLog(String(localized: "Copied command: \(r.command)"))
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
                for x in r.results where x.error == nil { addLog(String(localized: "Auto sync: \(describe(x, title: String(x.pairId.prefix(8))))")); changed = true }
                for x in r.results where x.error != nil { addLog(String(localized: "Auto sync skipped \(String(x.pairId.prefix(8))): \(x.error ?? "")"), isError: false) }
            }
        }
        if autoCreate {
            for s in scan.unpaired.claude + scan.unpaired.codex where s.active != true {
                if let r = try? await Engine.shared.create(from: s.side, id: s.id) {
                    addLog(r.side == "codex" ? String(localized: "Auto-created a Codex copy: \(s.title)") : String(localized: "Auto-created a Claude copy: \(s.title)"))
                    changed = true
                }
            }
        }
        if changed, let fresh = try? await Engine.shared.scan() { self.scan = fresh }
    }

    private func describe(_ r: SyncResult, title: String) -> String {
        let toCodex = r.pushedToCodex ?? 0, toClaude = r.pushedToClaude ?? 0
        if toCodex == 0 && toClaude == 0 { return String(localized: "\(title): already up to date.") }
        var parts: [String] = []
        if toCodex > 0 { parts.append(String(localized: "Claude → Codex \(toCodex) turns")) }
        if toClaude > 0 { parts.append(String(localized: "Codex → Claude \(toClaude) turns")) }
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
