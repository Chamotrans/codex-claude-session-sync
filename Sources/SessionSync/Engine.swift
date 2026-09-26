import Foundation

/// Thin wrapper around the Python engine (engine/sessionsync.py). Every call spawns a short-lived
/// process and decodes its JSON output.
final class Engine {
    static let shared = Engine()

    /// Python interpreter: the user's setting, else the first real python3 we can find. /usr/bin/python3 is the
    /// last resort because on a Mac without the Command Line Tools it is only an installer stub.
    var pythonPath: String {
        if let custom = UserDefaults.standard.string(forKey: "pythonPath"), !custom.isEmpty { return custom }
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/usr/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
    }

    static let pythonHelp = String(localized: "No usable python3 found. Install the Xcode Command Line Tools (run xcode-select --install in Terminal) or Homebrew Python (brew install python), then set the python3 path in Settings.")

    var scriptURL: URL {
        if let custom = UserDefaults.standard.string(forKey: "enginePath"), !custom.isEmpty {
            return URL(fileURLWithPath: custom)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("engine/sessionsync.py"),
           FileManager.default.fileExists(atPath: bundled.path) {
            return bundled
        }
        // Development fallback: repo checkout next to the home folder.
        return URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Documents/SessionSync/engine/sessionsync.py")
    }

    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    private func run(_ args: [String]) async throws -> Data {
        let script = scriptURL.path
        let python = pythonPath
        return try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: python)
                p.arguments = [script] + args
                var env = ProcessInfo.processInfo.environment
                env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                p.environment = env
                let out = Pipe(), err = Pipe()
                p.standardOutput = out
                p.standardError = err
                guard FileManager.default.isExecutableFile(atPath: python) else {
                    cont.resume(throwing: Failure(message: Engine.pythonHelp))
                    return
                }
                do {
                    try p.run()
                } catch {
                    cont.resume(throwing: Failure(message: String(localized: "Could not start the engine: \(error.localizedDescription)") + "\n" + Engine.pythonHelp))
                    return
                }
                // Drain stderr concurrently so a chatty engine can never block on a full pipe.
                var errData = Data()
                let errDone = DispatchSemaphore(value: 0)
                DispatchQueue.global(qos: .utility).async {
                    errData = err.fileHandleForReading.readDataToEndOfFile()
                    errDone.signal()
                }
                let data = out.fileHandleForReading.readDataToEndOfFile()
                errDone.wait()
                p.waitUntilExit()
                if let e = try? JSONDecoder().decode(EngineError.self, from: data) {
                    cont.resume(throwing: Failure(message: e.error))
                    return
                }
                if p.terminationStatus != 0 {
                    let msg = String(data: errData, encoding: .utf8) ?? ""
                    if msg.contains("xcode-select") || msg.contains("developer tools") || msg.contains("CommandLineTools") {
                        cont.resume(throwing: Failure(message: Engine.pythonHelp))
                    } else {
                        cont.resume(throwing: Failure(message: String(localized: "Engine error (\(Int(p.terminationStatus))): \(String(msg.suffix(400)))")))
                    }
                    return
                }
                cont.resume(returning: data)
            }
        }
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try dec.decode(type, from: data)
        } catch {
            let s = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw Failure(message: String(localized: "Could not read the engine output: \(error.localizedDescription)") + "\n" + s)
        }
    }

    func scan() async throws -> ScanResult {
        try decode(ScanResult.self, try await run(["scan"]))
    }

    func bootstrap() async throws -> ScanResult {
        struct Wrap: Decodable { var addedPairs: Int; var view: ScanResult }
        return try decode(Wrap.self, try await run(["bootstrap"])).view
    }

    func sync(pair: String, prefer: String? = nil, force: Bool = false) async throws -> SyncResult {
        var args = ["sync", "--pair", pair]
        if let prefer = prefer { args += ["--prefer", prefer] }
        if force { args.append("--force") }
        return try decode(SyncResult.self, try await run(args))
    }

    func syncAll() async throws -> SyncAllResult {
        try decode(SyncAllResult.self, try await run(["sync-all"]))
    }

    func create(from side: String, id: String) async throws -> CreateResult {
        try decode(CreateResult.self, try await run(["create", "--from", side, "--id", id]))
    }

    struct RecreateResult: Decodable { var recreated: String; var pairId: String }

    func recreate(pair: String) async throws -> RecreateResult {
        try decode(RecreateResult.self, try await run(["recreate", "--pair", pair]))
    }

    func link(claude: String, codex: String) async throws {
        _ = try await run(["link", "--claude", claude, "--codex", codex])
    }

    func unlink(pair: String) async throws {
        _ = try await run(["unlink", "--pair", pair])
    }

    func ignore(side: String, id: String) async throws {
        _ = try await run(["ignore", "--side", side, "--id", id])
    }

    struct RegisterResult: Decodable { var registered: String?; var already: Bool? }
    struct RegisterAllResult: Decodable { var registered: Int; var ids: [String] }

    func register(claudeId: String) async throws -> RegisterResult {
        try decode(RegisterResult.self, try await run(["register", "--id", claudeId]))
    }

    func registerAll() async throws -> RegisterAllResult {
        try decode(RegisterAllResult.self, try await run(["register-all"]))
    }

    func openCommand(side: String, id: String) async throws -> OpenResult {
        try decode(OpenResult.self, try await run(["open", "--side", side, "--id", id]))
    }
}
