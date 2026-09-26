import SwiftUI

struct ContentView: View {
    @EnvironmentObject var store: SyncStore
    @State private var showLog = false

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
        } content: {
            list
                .navigationSplitViewColumnWidth(min: 520, ideal: 640)
        } detail: {
            if let row = store.selectedRow {
                DetailView(row: row)
            } else {
                ContentUnavailableView("Select a conversation", systemImage: "arrow.left.arrow.right", description: Text("The list shows when each conversation was last edited in Claude and in Codex, so you can see at a glance which side is newer."))
            }
        }
        .toolbar { toolbar }
        .searchable(text: $store.search, placement: .toolbar, prompt: "Search titles or projects")
        .sheet(item: $store.pendingConflict) { pair in ConflictSheet(pair: pair) }
        .sheet(isPresented: $showLog) { LogSheet() }
        .overlay(alignment: .bottom) { statusBar }
    }

    // MARK: sidebar

    private var sidebar: some View {
        List(selection: $store.filter) {
            Section("Status") {
                ForEach(RowFilter.allCases) { f in
                    Label {
                        HStack {
                            Text(f.label)
                            Spacer()
                            Text("\(store.count(for: f))")
                                .font(.caption).monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: f.symbol)
                            .foregroundStyle(color(for: f))
                    }
                    .tag(f)
                }
            }
            if let t = store.scan?.totals {
                Section("Totals") {
                    LabeledContent("Claude", value: "\(t.claude)")
                    LabeledContent("Codex", value: "\(t.codex)")
                    LabeledContent("Paired", value: "\(t.pairs)")
                }
                .font(.caption)
            }
        }
        .listStyle(.sidebar)
    }

    private func color(for f: RowFilter) -> Color {
        switch f {
        case .conflicts: return .red
        case .needsSync: return .orange
        case .inSync: return .green
        case .onlyClaude: return .purple
        case .onlyCodex: return .teal
        case .all, .echoes: return .secondary
        }
    }

    // MARK: list

    private var list: some View {
        Table(store.rows, selection: $store.selection) {
            TableColumn("Conversation") { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.title.isEmpty ? String(localized: "(untitled)") : row.title).lineLimit(1)
                    Text(row.projectName).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                .padding(.vertical, 2)
            }
            .width(min: 200, ideal: 280)

            TableColumn("Claude") { row in
                SideCell(session: row.claude, isNewer: isNewer(row, side: "claude"), active: isActive(row, side: "claude"))
            }
            .width(min: 120, ideal: 140)

            TableColumn("Codex") { row in
                SideCell(session: row.codex, isNewer: isNewer(row, side: "codex"), active: isActive(row, side: "codex"))
            }
            .width(min: 120, ideal: 140)

            TableColumn("Status") { row in
                StatusPill(row: row)
            }
            .width(min: 110, ideal: 130)

            TableColumn("") { row in
                ActionButton(row: row)
            }
            .width(min: 96, ideal: 110)
        }
        .contextMenu(forSelectionType: SyncRow.ID.self) { ids in
            if let id = ids.first, let row = store.rows.first(where: { $0.id == id }) {
                RowMenu(row: row)
            }
        }
        .overlay {
            if store.scan == nil {
                ProgressView("Scanning for the first time…")
            } else if store.rows.isEmpty {
                ContentUnavailableView.search
            }
        }
    }

    private func isNewer(_ row: SyncRow, side: String) -> Bool {
        guard case .pair(let p) = row else { return false }
        switch p.status {
        case .claudeNewer: return side == "claude"
        case .codexNewer: return side == "codex"
        case .conflict:
            let c = p.claude?.lastActivityDate ?? .distantPast
            let d = p.codex?.lastActivityDate ?? .distantPast
            return side == "claude" ? c > d : d > c
        default: return false
        }
    }

    private func isActive(_ row: SyncRow, side: String) -> Bool {
        switch row {
        case .pair(let p): return side == "claude" ? p.claudeActive : p.codexActive
        case .only(let s): return s.side == side && (s.active ?? false)
        }
    }

    // MARK: toolbar & status

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            Button { store.refresh() } label: { Label("Rescan", systemImage: "arrow.clockwise") }
                .disabled(store.isBusy)
            Button { store.syncAll() } label: { Label("Sync All", systemImage: "arrow.triangle.2.circlepath") }
                .disabled(store.isBusy)
            Toggle(isOn: $store.autoSync) { Label("Auto Sync", systemImage: store.autoSync ? "icloud.fill" : "icloud") }
                .toggleStyle(.button)
                .help("When on, every scan pushes newer turns to the other side (conflicts and open conversations are skipped)")
            Button { store.registerAllInDesktop() } label: {
                Label(store.missingFromDesktopCount > 0 ? String(localized: "Register Sidebar (\(store.missingFromDesktopCount))") : String(localized: "Register Sidebar"), systemImage: "sidebar.left")
            }
            .disabled(store.isBusy || store.missingFromDesktopCount == 0)
            .help("Add conversations that came from Codex to the Claude desktop sidebar (other CLI sessions can be registered one by one from the context menu)")
            Button { showLog.toggle() } label: { Label("Activity", systemImage: "list.bullet.rectangle") }
        }
    }

    private var statusBar: some View {
        HStack(spacing: 10) {
            if store.isBusy {
                ProgressView().controlSize(.small)
                Text(store.busyText)
            } else if let err = store.lastError {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                Text(err).lineLimit(1)
            } else if let last = store.log.first {
                Image(systemName: last.isError ? "xmark.circle" : "checkmark.circle").foregroundStyle(last.isError ? .red : .green)
                Text(last.text).lineLimit(1)
            } else {
                Text("Ready")
            }
            Spacer()
            if let d = store.lastRefresh {
                Text("Last scan \(RelativeTime.string(d))").foregroundStyle(.secondary)
            }
            if store.autoSync {
                Label("Auto sync on", systemImage: "icloud.fill").foregroundStyle(.blue)
            }
        }
        .font(.caption)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(.bar)
    }
}

// MARK: - cells

struct SideCell: View {
    let session: SessionInfo?
    let isNewer: Bool
    let active: Bool

    var body: some View {
        if let s = session {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    if isNewer { Image(systemName: "arrow.up.circle.fill").foregroundStyle(.orange) }
                    Text(RelativeTime.string(s.lastActivityDate))
                        .fontWeight(isNewer ? .semibold : .regular)
                    if active { Image(systemName: "circle.fill").font(.system(size: 7)).foregroundStyle(.green).help("Open") }
                    if s.side == "claude" && !s.isInDesktopSidebar {
                        Image(systemName: "sidebar.left").font(.caption2).foregroundStyle(.secondary)
                            .help("Not in the Claude desktop sidebar (open it with claude --resume)")
                    }
                }
                Text("\(s.turns) turns · \(ByteCount.string(s.size))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .help(RelativeTime.full(s.lastActivityDate))
        } else {
            Text("—").foregroundStyle(.tertiary)
        }
    }
}

struct StatusPill: View {
    let row: SyncRow

    var body: some View {
        let (text, color, symbol) = descriptor
        Label(text, systemImage: symbol)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
            .lineLimit(1)
    }

    private var descriptor: (String, Color, String) {
        switch row {
        case .pair(let p):
            switch p.status {
            case .inSync: return (String(localized: "In sync"), .green, "checkmark.circle.fill")
            case .claudeNewer: return ("Claude → Codex \(p.claudeNewTurns)", .orange, "arrow.right.circle.fill")
            case .codexNewer: return ("Codex → Claude \(p.codexNewTurns)", .orange, "arrow.left.circle.fill")
            case .conflict: return (String(localized: "Conflict \(p.claudeNewTurns)/\(p.codexNewTurns)"), .red, "exclamationmark.triangle.fill")
            case .missing:
                let text = p.missingSide == "codex" ? String(localized: "Codex side deleted")
                    : (p.missingSide == "claude" ? String(localized: "Claude side deleted") : String(localized: "Both sides deleted"))
                return (text, .gray, "questionmark.circle")
            case .unknown: return (String(localized: "Unknown"), .gray, "questionmark.circle")
            case .rebased: return (String(localized: "Needs rebase"), .gray, "arrow.counterclockwise.circle")
            }
        case .only(let s):
            if s.isEcho { return (String(localized: "Re-import"), .gray, "arrow.triangle.branch") }
            return s.side == "claude" ? (String(localized: "Only in Claude"), .purple, "c.circle.fill") : (String(localized: "Only in Codex"), .teal, "x.circle.fill")
        }
    }
}

struct ActionButton: View {
    @EnvironmentObject var store: SyncStore
    let row: SyncRow

    var body: some View {
        switch row {
        case .pair(let p):
            switch p.status {
            case .claudeNewer, .codexNewer, .rebased:
                Button("Sync") { store.syncPair(p) }.disabled(store.isBusy)
            case .conflict:
                Button("Resolve") { store.pendingConflict = p }.disabled(store.isBusy)
            case .missing where p.missingSide == "claude" || p.missingSide == "codex":
                Button("Recreate") { store.recreate(p) }.disabled(store.isBusy)
                    .help("Rebuild the deleted side from the side that still exists")
            default:
                EmptyView()
            }
        case .only(let s) where s.isEcho:
            EmptyView()
        case .only(let s):
            Button(s.side == "claude" ? "→ Codex" : "→ Claude") { store.create(from: s) }
                .disabled(store.isBusy || (s.active ?? false))
                .help((s.active ?? false) ? String(localized: "This conversation is open; try again later") : String(localized: "Create a copy of this conversation on the other side"))
        }
    }
}

struct RowMenu: View {
    @EnvironmentObject var store: SyncStore
    let row: SyncRow

    var body: some View {
        if let c = row.claude {
            Button("Copy Claude Resume Command") { store.copyResumeCommand(c) }
            Button("Show Claude File in Finder") { store.revealInFinder(c) }
            if !c.isInDesktopSidebar {
                Button("Add to Claude Desktop Sidebar") { store.registerInDesktop(c) }
            }
        }
        if let d = row.codex {
            Button("Copy Codex Resume Command") { store.copyResumeCommand(d) }
            Button("Show Codex File in Finder") { store.revealInFinder(d) }
        }
        Divider()
        switch row {
        case .pair(let p):
            Button("Unlink") { store.unlink(p) }
        case .only(let s):
            Button("Hide This Conversation") { store.ignore(s) }
        }
    }
}

// MARK: - sheets

struct ConflictSheet: View {
    @EnvironmentObject var store: SyncStore
    @Environment(\.dismiss) private var dismiss
    let pair: PairInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Both sides have new turns", systemImage: "exclamationmark.triangle.fill")
                .font(.title3.weight(.semibold)).foregroundStyle(.red)
            Text(pair.title).font(.headline)
            Text("Since the last sync, Claude gained \(pair.claudeNewTurns) turns and Codex gained \(pair.codexNewTurns). How do you want to resolve it?")
            VStack(alignment: .leading, spacing: 8) {
                choice("Merge both (recommended)", "Each side gets the other side's new turns. Both end up complete, only the order differs.", "both")
                choice("Keep Claude", "Only Claude's new turns go to Codex. Codex's new turns stay in Codex.", "claude")
                choice("Keep Codex", "Only Codex's new turns go to Claude. Claude's new turns stay in Claude.", "codex")
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func choice(_ title: LocalizedStringKey, _ desc: LocalizedStringKey, _ prefer: String) -> some View {
        Button {
            dismiss()
            store.syncPair(pair, prefer: prefer)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.medium)
                Text(desc).font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
        }
        .buttonStyle(.bordered)
    }
}

struct LogSheet: View {
    @EnvironmentObject var store: SyncStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading) {
            HStack {
                Text("Activity").font(.headline)
                Spacer()
                Button("Clear") { store.log.removeAll() }
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            List(store.log) { e in
                HStack(alignment: .top) {
                    Text(RelativeTime.full(e.date)).font(.caption).foregroundStyle(.secondary).frame(width: 120, alignment: .leading)
                    Text(e.text).foregroundStyle(e.isError ? .red : .primary).textSelection(.enabled)
                }
            }
        }
        .padding(16)
        .frame(width: 640, height: 400)
    }
}
