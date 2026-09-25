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
                ContentUnavailableView("揀一個工作階段", systemImage: "arrow.left.arrow.right", description: Text("左邊列表會顯示每個對話喺 Claude 同 Codex 兩邊嘅最後編輯時間，邊個新啲一目了然。"))
            }
        }
        .toolbar { toolbar }
        .searchable(text: $store.search, placement: .toolbar, prompt: "搜尋標題或項目")
        .sheet(item: $store.pendingConflict) { pair in ConflictSheet(pair: pair) }
        .sheet(isPresented: $showLog) { LogSheet() }
        .overlay(alignment: .bottom) { statusBar }
    }

    // MARK: sidebar

    private var sidebar: some View {
        List(selection: $store.filter) {
            Section("狀態") {
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
                Section("總數") {
                    LabeledContent("Claude", value: "\(t.claude)")
                    LabeledContent("Codex", value: "\(t.codex)")
                    LabeledContent("已配對", value: "\(t.pairs)")
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
        case .all: return .secondary
        }
    }

    // MARK: list

    private var list: some View {
        Table(store.rows, selection: $store.selection) {
            TableColumn("對話") { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.title.isEmpty ? "(無標題)" : row.title).lineLimit(1)
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

            TableColumn("狀態") { row in
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
                ProgressView("首次掃描中…")
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
            Button { store.refresh() } label: { Label("重新掃描", systemImage: "arrow.clockwise") }
                .disabled(store.isBusy)
            Button { store.syncAll() } label: { Label("同步全部", systemImage: "arrow.triangle.2.circlepath") }
                .disabled(store.isBusy)
            Toggle(isOn: $store.autoSync) { Label("自動同步", systemImage: store.autoSync ? "icloud.fill" : "icloud") }
                .toggleStyle(.button)
                .help("開啟後每次掃描都會自動推送較新嘅回合去另一邊（衝突同使用中嘅對話會略過）")
            Button { store.registerAllInDesktop() } label: {
                Label(store.missingFromDesktopCount > 0 ? "登記側欄 (\(store.missingFromDesktopCount))" : "登記側欄", systemImage: "sidebar.left")
            }
            .disabled(store.isBusy || store.missingFromDesktopCount == 0)
            .help("將由 Codex 同步過嚟、但 Claude Desktop 側欄未有嘅對話登記到側欄（其他 CLI 對話可以逐個喺右鍵選單登記）")
            Button { showLog.toggle() } label: { Label("記錄", systemImage: "list.bullet.rectangle") }
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
                Text("就緒")
            }
            Spacer()
            if let d = store.lastRefresh {
                Text("上次掃描 " + RelativeTime.string(d)).foregroundStyle(.secondary)
            }
            if store.autoSync {
                Label("自動同步開啟", systemImage: "icloud.fill").foregroundStyle(.blue)
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
                    if active { Image(systemName: "circle.fill").font(.system(size: 7)).foregroundStyle(.green).help("使用中") }
                    if s.side == "claude" && !s.isInDesktopSidebar {
                        Image(systemName: "sidebar.left").font(.caption2).foregroundStyle(.secondary)
                            .help("Claude Desktop 側欄未登記（只可經 claude --resume 開啟）")
                    }
                }
                Text("\(s.turns) 回合 · \(ByteCount.string(s.size))")
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
            case .inSync: return ("已同步", .green, "checkmark.circle.fill")
            case .claudeNewer: return ("Claude → Codex \(p.claudeNewTurns)", .orange, "arrow.right.circle.fill")
            case .codexNewer: return ("Codex → Claude \(p.codexNewTurns)", .orange, "arrow.left.circle.fill")
            case .conflict: return ("衝突 \(p.claudeNewTurns)/\(p.codexNewTurns)", .red, "exclamationmark.triangle.fill")
            case .missing: return ("缺少一邊", .gray, "questionmark.circle")
            case .rebased: return ("需重設", .gray, "arrow.counterclockwise.circle")
            }
        case .only(let s):
            return s.side == "claude" ? ("只有 Claude", .purple, "c.circle.fill") : ("只有 Codex", .teal, "x.circle.fill")
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
                Button("同步") { store.syncPair(p) }.disabled(store.isBusy)
            case .conflict:
                Button("解決") { store.pendingConflict = p }.disabled(store.isBusy)
            default:
                EmptyView()
            }
        case .only(let s):
            Button(s.side == "claude" ? "→ Codex" : "→ Claude") { store.create(from: s) }
                .disabled(store.isBusy || (s.active ?? false))
                .help((s.active ?? false) ? "對話使用中，請稍後再試" : "喺另一邊建立呢個對話嘅副本")
        }
    }
}

struct RowMenu: View {
    @EnvironmentObject var store: SyncStore
    let row: SyncRow

    var body: some View {
        if let c = row.claude {
            Button("複製 Claude resume 指令") { store.copyResumeCommand(c) }
            Button("喺 Finder 顯示 Claude 檔案") { store.revealInFinder(c) }
            if !c.isInDesktopSidebar {
                Button("登記到 Claude Desktop 側欄") { store.registerInDesktop(c) }
            }
        }
        if let d = row.codex {
            Button("複製 Codex resume 指令") { store.copyResumeCommand(d) }
            Button("喺 Finder 顯示 Codex 檔案") { store.revealInFinder(d) }
        }
        Divider()
        switch row {
        case .pair(let p):
            Button("解除配對") { store.unlink(p) }
        case .only(let s):
            Button("隱藏呢個工作階段") { store.ignore(s) }
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
            Label("兩邊都有新回合", systemImage: "exclamationmark.triangle.fill")
                .font(.title3.weight(.semibold)).foregroundStyle(.red)
            Text(pair.title).font(.headline)
            Text("自上次同步後，Claude 多咗 \(pair.claudeNewTurns) 個回合，Codex 多咗 \(pair.codexNewTurns) 個回合。你想點處理？")
            VStack(alignment: .leading, spacing: 8) {
                choice("合併兩邊（建議）", "兩邊都會補上對方嘅新回合，兩邊內容都齊，只係次序唔同。", "both")
                choice("以 Claude 為準", "只將 Claude 嘅新回合推去 Codex；Codex 嘅新回合會留喺 Codex 唔會推過嚟。", "claude")
                choice("以 Codex 為準", "只將 Codex 嘅新回合推去 Claude；Claude 嘅新回合會留喺 Claude。", "codex")
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func choice(_ title: String, _ desc: String, _ prefer: String) -> some View {
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
                Text("活動記錄").font(.headline)
                Spacer()
                Button("清除") { store.log.removeAll() }
                Button("關閉") { dismiss() }.keyboardShortcut(.cancelAction)
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
