import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: SyncStore
    @AppStorage("pythonPath") private var pythonPath = ""
    @AppStorage("enginePath") private var enginePath = ""

    var body: some View {
        Form {
            Section("Auto sync") {
                Toggle("Push newer turns automatically after every scan", isOn: $store.autoSync)
                Toggle("Create counterparts for one-sided conversations automatically", isOn: $store.autoCreate)
                    .disabled(!store.autoSync)
                HStack {
                    Slider(value: $store.refreshInterval, in: 15...600, step: 15) { Text("Scan interval") }
                    Text("\(Int(store.refreshInterval)) s").monospacedDigit().frame(width: 60, alignment: .trailing)
                }
                .onChange(of: store.refreshInterval) { _, _ in store.startTimer() }
                Text("Besides the timer, the app watches ~/.claude/projects and ~/.codex/sessions and rescans a few seconds after files change. Conflicts and open conversations are never synced automatically.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Engine") {
                TextField("python3 path (empty = detect automatically)", text: $pythonPath)
                TextField("sessionsync.py path (empty = bundled)", text: $enginePath)
                LabeledContent("Engine in use", value: Engine.shared.scriptURL.path)
                    .font(.caption)
                LabeledContent("Python in use", value: Engine.shared.pythonPath)
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .padding()
    }
}
