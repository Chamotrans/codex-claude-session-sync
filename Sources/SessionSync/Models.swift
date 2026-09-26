import Foundation

/// One session as reported by the engine, on either side.
struct SessionInfo: Codable, Identifiable, Hashable {
    var id: String
    var side: String
    var path: String
    var cwd: String
    var title: String
    var firstPrompt: String?
    var turns: Int
    var lastActivity: String?
    var size: Int
    var active: Bool?
    var imported: Bool?
    var archived: Bool?
    var inDesktop: Bool?
    /// Codex only: set when this thread is Codex Desktop's re-import of an already-synced session (pair id).
    var echoOf: String?
    var isEcho: Bool { echoOf != nil }

    /// Claude sessions only: true when the Claude desktop app's sidebar knows about this session.
    var isInDesktopSidebar: Bool { inDesktop ?? true }

    var lastActivityDate: Date? { ISO8601.parse(lastActivity) }
    var projectName: String { (cwd as NSString).lastPathComponent.isEmpty ? cwd : (cwd as NSString).lastPathComponent }
}

enum PairStatus: String, Codable, CaseIterable {
    case inSync = "in_sync"
    case claudeNewer = "claude_newer"
    case codexNewer = "codex_newer"
    case conflict
    case missing
    case rebased
    case unknown

    /// Unknown values from a newer engine decode as `.unknown` instead of failing the whole scan.
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = PairStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .inSync: return "已同步"
        case .claudeNewer: return "Claude 較新"
        case .codexNewer: return "Codex 較新"
        case .conflict: return "衝突"
        case .missing: return "缺少一邊"
        case .rebased: return "需重設基準"
        case .unknown: return "未知"
        }
    }

    var needsAction: Bool { self != .inSync }
}

struct PairInfo: Codable, Identifiable, Hashable {
    var pairId: String
    var status: PairStatus
    var claude: SessionInfo?
    var codex: SessionInfo?
    var missingSide: String?
    var syncedClaudeTurns: Int
    var syncedCodexTurns: Int
    var lastSync: String?
    var origin: String?
    var claudeActive: Bool
    var codexActive: Bool
    var title: String
    var cwd: String

    var id: String { pairId }
    var newestDate: Date? {
        [claude?.lastActivityDate, codex?.lastActivityDate].compactMap { $0 }.max()
    }
    var claudeNewTurns: Int { max(0, (claude?.turns ?? 0) - syncedClaudeTurns) }
    var codexNewTurns: Int { max(0, (codex?.turns ?? 0) - syncedCodexTurns) }
    var projectName: String { (cwd as NSString).lastPathComponent.isEmpty ? cwd : (cwd as NSString).lastPathComponent }
}

struct Unpaired: Codable, Hashable {
    var claude: [SessionInfo]
    var codex: [SessionInfo]
}

struct Totals: Codable, Hashable {
    var claude: Int
    var codex: Int
    var pairs: Int
    var echoes: Int?
}

struct ScanResult: Codable, Hashable {
    var generatedAt: String
    var pairs: [PairInfo]
    var unpaired: Unpaired
    var counts: [String: Int]
    var totals: Totals
    var echoes: [SessionInfo]?
}

struct SyncResult: Codable {
    var pairId: String
    var before: String?
    var after: String?
    var pushedToCodex: Int?
    var pushedToClaude: Int?
    var error: String?
}

struct SyncAllResult: Codable {
    var results: [SyncResult]
}

struct CreateResult: Codable {
    var created: String
    var side: String
}

struct OpenResult: Codable {
    var command: String
    var cwd: String
}

struct EngineError: Codable, Error, LocalizedError {
    var error: String
    var errorDescription: String? { error }
}

/// A row in the unified list: either a linked pair or a session that only exists on one side.
enum SyncRow: Identifiable, Hashable {
    case pair(PairInfo)
    case only(SessionInfo)

    var id: String {
        switch self {
        case .pair(let p): return "pair:" + p.pairId
        case .only(let s): return "only:" + s.side + ":" + s.id
        }
    }

    var title: String {
        switch self {
        case .pair(let p): return p.title
        case .only(let s): return s.title
        }
    }

    var projectName: String {
        switch self {
        case .pair(let p): return p.projectName
        case .only(let s): return s.projectName
        }
    }

    var newestDate: Date? {
        switch self {
        case .pair(let p): return p.newestDate
        case .only(let s): return s.lastActivityDate
        }
    }

    var claude: SessionInfo? {
        switch self {
        case .pair(let p): return p.claude
        case .only(let s): return s.side == "claude" ? s : nil
        }
    }

    var codex: SessionInfo? {
        switch self {
        case .pair(let p): return p.codex
        case .only(let s): return s.side == "codex" ? s : nil
        }
    }
}

enum RowFilter: String, CaseIterable, Identifiable {
    case all, needsSync, conflicts, onlyClaude, onlyCodex, inSync, echoes
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "全部"
        case .needsSync: return "待同步"
        case .conflicts: return "衝突"
        case .onlyClaude: return "只有 Claude"
        case .onlyCodex: return "只有 Codex"
        case .inSync: return "已同步"
        case .echoes: return "重複匯入"
        }
    }
    var symbol: String {
        switch self {
        case .all: return "tray.full"
        case .needsSync: return "arrow.triangle.2.circlepath"
        case .conflicts: return "exclamationmark.triangle"
        case .onlyClaude: return "c.circle"
        case .onlyCodex: return "x.circle"
        case .inSync: return "checkmark.circle"
        case .echoes: return "arrow.triangle.branch"
        }
    }
}

enum ISO8601 {
    private static let withFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    static func parse(_ s: String?) -> Date? {
        guard let s = s, !s.isEmpty else { return nil }
        return withFrac.date(from: s) ?? plain.date(from: s)
    }
}

enum RelativeTime {
    private static let formatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f
    }()
    private static let absolute: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .short
        f.timeStyle = .short
        return f
    }()
    static func string(_ d: Date?) -> String {
        guard let d = d else { return "—" }
        return formatter.localizedString(for: d, relativeTo: Date())
    }
    static func full(_ d: Date?) -> String {
        guard let d = d else { return "—" }
        return absolute.string(from: d)
    }
}

enum ByteCount {
    static func string(_ n: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file)
    }
}
