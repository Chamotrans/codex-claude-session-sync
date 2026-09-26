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
            Text(row.title.isEmpty ? String(localized: "(untitled)") : row.title).font(.title2.weight(.semibold)).textSelection(.enabled)
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
        GroupBox("Sync status") {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("Last sync").foregroundStyle(.secondary)
                    Text(RelativeTime.full(ISO8601.parse(p.lastSync)))
                }
                GridRow {
                    Text("Sync baseline").foregroundStyle(.secondary)
                    Text("Claude \(p.syncedClaudeTurns) turns · Codex \(p.syncedCodexTurns) turns")
                }
                GridRow {
                    Text("Not yet synced").foregroundStyle(.secondary)
                    Text(unsyncedText(p)).foregroundStyle(p.status == .inSync ? .green : .orange)
                }
                if let o = p.origin {
                    GridRow {
                        Text("Paired via").foregroundStyle(.secondary)
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
        case .inSync: return String(localized: "Nothing, both sides match")
        case .claudeNewer: return String(localized: "Claude has \(p.claudeNewTurns) new turns not yet in Codex")
        case .codexNewer: return String(localized: "Codex has \(p.codexNewTurns) new turns not yet in Claude")
        case .conflict: return String(localized: "Both sides have new turns (Claude \(p.claudeNewTurns), Codex \(p.codexNewTurns))")
        case .missing:
            switch p.missingSide {
            case "codex": return String(localized: "The Codex thread was deleted. Recreate it from Claude, or unlink the pair.")
            case "claude": return String(localized: "The Claude session was deleted. Recreate it from Codex, or unlink the pair.")
            default: return String(localized: "Neither side exists any more. Unlink the pair.")
            }
        case .unknown: return String(localized: "The engine reported a status this app does not know. Update the app.")
        case .rebased: return String(localized: "One side has fewer turns than the baseline; the next sync re-bases it")
        }
    }

    private func originLabel(_ o: String) -> String {
        switch o {
        case "bootstrap_same_id": return String(localized: "Same id (imported by codex2claude)")
        case "bootstrap_codex_import": return String(localized: "Codex auto-imported the Claude conversation")
        case "created_codex": return String(localized: "Codex copy created by this app")
        case "created_claude": return String(localized: "Claude copy created by this app")
        case "manual": return String(localized: "Linked manually")
        default: return o
        }
    }

    private var actions: some View {
        HStack {
            switch row {
            case .pair(let p):
                if p.status == .conflict {
                    Button("Resolve Conflict…") { store.pendingConflict = p }.buttonStyle(.borderedProminent)
                } else if p.status.needsAction {
                    Button("Sync Now") { store.syncPair(p) }.buttonStyle(.borderedProminent)
                }
                if p.status == .missing && (p.missingSide == "claude" || p.missingSide == "codex") {
                    Button(p.missingSide == "codex" ? "Recreate Codex Copy" : "Recreate Claude Copy") { store.recreate(p) }
                        .buttonStyle(.borderedProminent)
                }
                Button("Unlink") { store.unlink(p) }
            case .only(let s) where s.isEcho:
                Label("Codex Desktop imported this conversation again. The original is already synced, so this copy is never synced or copied. You can archive it in Codex.",
                      systemImage: "arrow.triangle.branch")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Hide") { store.ignore(s) }
            case .only(let s):
                Button(s.side == "claude" ? "Create Codex Copy" : "Create Claude Copy") { store.create(from: s) }
                    .buttonStyle(.borderedProminent)
                    .disabled(s.active ?? false)
                Button("Hide") { store.ignore(s) }
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
                            Label("Newer", systemImage: "arrow.up.circle.fill").font(.caption).foregroundStyle(.orange)
                        }
                        if active {
                            Label("Open", systemImage: "circle.fill").font(.caption).foregroundStyle(.green)
                        }
                    }
                    Text(RelativeTime.string(s.lastActivityDate)).font(.caption).foregroundStyle(.secondary)
                    Divider()
                    row("Turns", "\(s.turns)")
                    row("Size", ByteCount.string(s.size))
                    row("ID", String(s.id.prefix(8)) + "…")
                    row("Folder", s.cwd)
                    if s.side == "claude" && !s.isInDesktopSidebar {
                        Label("Not in the Claude desktop sidebar", systemImage: "sidebar.left")
                            .font(.caption).foregroundStyle(.orange)
                    }
                    HStack {
                        Button("Copy Resume Command") { store.copyResumeCommand(s) }
                        Button("Finder") { store.revealInFinder(s) }
                        if s.side == "claude" && !s.isInDesktopSidebar {
                            Button("Add to Sidebar") { store.registerInDesktop(s) }
                        }
                    }
                    .controlSize(.small)
                    .padding(.top, 4)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(spacing: 6) {
                    Image(systemName: "questionmark.circle").font(.largeTitle).foregroundStyle(.tertiary)
                    Text("Not in \(name) yet").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 120)
            }
        } label: {
            Label(name, systemImage: side == "claude" ? "c.circle.fill" : "x.circle.fill").foregroundStyle(tint)
        }
    }

    private func row(_ k: LocalizedStringKey, _ v: String) -> some View {
        HStack(alignment: .top) {
            Text(k).foregroundStyle(.secondary).frame(width: 52, alignment: .leading)
            Text(v).textSelection(.enabled).lineLimit(2)
        }
        .font(.callout)
    }
}
