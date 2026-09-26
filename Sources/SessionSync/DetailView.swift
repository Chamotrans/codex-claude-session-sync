import SwiftUI

struct DetailView: View {
    @EnvironmentObject var store: SyncStore
    let row: SyncRow

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                HStack(alignment: .top, spacing: 12) {
                    SideCard(side: "claude", session: row.claude, isNewer: newerSide == "claude", active: active("claude"))
                    SideCard(side: "codex", session: row.codex, isNewer: newerSide == "codex", active: active("codex"))
                }
                if case .pair(let p) = row { pairInfo(p) }
                actions
            }
            .padding(20)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(row.title.isEmpty ? "(無標題)" : row.title).font(.title2.weight(.semibold)).textSelection(.enabled)
            HStack(spacing: 8) {
                StatusPill(row: row)
                Text(row.projectName).foregroundStyle(.secondary)
            }
        }
    }

    private var newerSide: String? {
        let c = row.claude?.lastActivityDate, d = row.codex?.lastActivityDate
        guard let c = c, let d = d else { return nil }
        if abs(c.timeIntervalSince(d)) < 1 { return nil }
        return c > d ? "claude" : "codex"
    }

    private func active(_ side: String) -> Bool {
        switch row {
        case .pair(let p): return side == "claude" ? p.claudeActive : p.codexActive
        case .only(let s): return s.side == side && (s.active ?? false)
        }
    }

    @ViewBuilder
    private func pairInfo(_ p: PairInfo) -> some View {
        GroupBox("同步狀態") {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("上次同步").foregroundStyle(.secondary)
                    Text(RelativeTime.full(ISO8601.parse(p.lastSync)))
                }
                GridRow {
                    Text("同步基準").foregroundStyle(.secondary)
                    Text("Claude \(p.syncedClaudeTurns) 回合 · Codex \(p.syncedCodexTurns) 回合")
                }
                GridRow {
                    Text("未同步").foregroundStyle(.secondary)
                    Text(unsyncedText(p)).foregroundStyle(p.status == .inSync ? .green : .orange)
                }
                if let o = p.origin {
                    GridRow {
                        Text("配對來源").foregroundStyle(.secondary)
                        Text(originLabel(o))
                    }
                }
            }
            .font(.callout)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func unsyncedText(_ p: PairInfo) -> String {
        switch p.status {
        case .inSync: return "冇，兩邊一樣"
        case .claudeNewer: return "Claude 有 \(p.claudeNewTurns) 個新回合未推去 Codex"
        case .codexNewer: return "Codex 有 \(p.codexNewTurns) 個新回合未推去 Claude"
        case .conflict: return "兩邊都有新回合（Claude \(p.claudeNewTurns)、Codex \(p.codexNewTurns)）"
        case .missing: return "其中一邊搵唔到檔案"
        case .rebased: return "有一邊嘅回合數少過基準，同步時會重設"
        }
    }

    private func originLabel(_ o: String) -> String {
        switch o {
        case "bootstrap_same_id": return "同一 ID（由 codex2claude 匯入）"
        case "bootstrap_codex_import": return "Codex 自動匯入 Claude 對話"
        case "created_codex": return "由 SessionSync 建立 Codex 副本"
        case "created_claude": return "由 SessionSync 建立 Claude 副本"
        case "manual": return "手動配對"
        default: return o
        }
    }

    private var actions: some View {
        HStack {
            switch row {
            case .pair(let p):
                if p.status == .conflict {
                    Button("解決衝突…") { store.pendingConflict = p }.buttonStyle(.borderedProminent)
                } else if p.status.needsAction {
                    Button("立即同步") { store.syncPair(p) }.buttonStyle(.borderedProminent)
                }
                Button("解除配對") { store.unlink(p) }
            case .only(let s) where s.isEcho:
                Label("呢個係 Codex Desktop 重複匯入嘅副本，原本嘅對話已經同步緊，唔會再同步或者複製。可以喺 Codex 封存。",
                      systemImage: "arrow.triangle.branch")
                    .font(.callout).foregroundStyle(.secondary)
                Button("隱藏") { store.ignore(s) }
            case .only(let s):
                Button(s.side == "claude" ? "喺 Codex 建立副本" : "喺 Claude 建立副本") { store.create(from: s) }
                    .buttonStyle(.borderedProminent)
                    .disabled(s.active ?? false)
                Button("隱藏") { store.ignore(s) }
            }
            Spacer()
        }
        .disabled(store.isBusy)
    }
}

struct SideCard: View {
    @EnvironmentObject var store: SyncStore
    let side: String
    let session: SessionInfo?
    let isNewer: Bool
    let active: Bool

    private var name: String { side == "claude" ? "Claude Code" : "Codex" }
    private var tint: Color { side == "claude" ? .purple : .teal }

    var body: some View {
        GroupBox {
            if let s = session {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(RelativeTime.full(s.lastActivityDate)).font(.headline)
                        if isNewer {
                            Label("較新", systemImage: "arrow.up.circle.fill").font(.caption).foregroundStyle(.orange)
                        }
                        if active {
                            Label("使用中", systemImage: "circle.fill").font(.caption).foregroundStyle(.green)
                        }
                    }
                    Text(RelativeTime.string(s.lastActivityDate)).font(.caption).foregroundStyle(.secondary)
                    Divider()
                    row("回合", "\(s.turns)")
                    row("大小", ByteCount.string(s.size))
                    row("ID", String(s.id.prefix(8)) + "…")
                    row("目錄", s.cwd)
                    if s.side == "claude" && !s.isInDesktopSidebar {
                        Label("未喺 Claude Desktop 側欄", systemImage: "sidebar.left")
                            .font(.caption).foregroundStyle(.orange)
                    }
                    HStack {
                        Button("複製 resume 指令") { store.copyResumeCommand(s) }
                        Button("Finder") { store.revealInFinder(s) }
                        if s.side == "claude" && !s.isInDesktopSidebar {
                            Button("登記到側欄") { store.registerInDesktop(s) }
                        }
                    }
                    .controlSize(.small)
                    .padding(.top, 4)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(spacing: 6) {
                    Image(systemName: "questionmark.circle").font(.largeTitle).foregroundStyle(.tertiary)
                    Text("\(name) 度未有呢個對話").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 120)
            }
        } label: {
            Label(name, systemImage: side == "claude" ? "c.circle.fill" : "x.circle.fill").foregroundStyle(tint)
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top) {
            Text(k).foregroundStyle(.secondary).frame(width: 36, alignment: .leading)
            Text(v).textSelection(.enabled).lineLimit(2)
        }
        .font(.callout)
    }
}
