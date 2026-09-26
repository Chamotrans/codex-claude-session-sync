import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: SyncStore
    @AppStorage("pythonPath") private var pythonPath = ""
    @AppStorage("enginePath") private var enginePath = ""

    var body: some View {
        Form {
            Section("自動同步") {
                Toggle("每次掃描後自動推送較新嘅回合", isOn: $store.autoSync)
                Toggle("自動為只有一邊嘅對話建立副本", isOn: $store.autoCreate)
                    .disabled(!store.autoSync)
                HStack {
                    Slider(value: $store.refreshInterval, in: 15...600, step: 15) { Text("掃描間隔") }
                    Text("\(Int(store.refreshInterval)) 秒").monospacedDigit().frame(width: 60, alignment: .trailing)
                }
                .onChange(of: store.refreshInterval) { _, _ in store.startTimer() }
                Text("除咗定時掃描，app 亦會監察 ~/.claude/projects 同 ~/.codex/sessions 嘅檔案變動，有更新會延遲幾秒自動掃描。衝突同使用中嘅對話永遠唔會自動同步。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("引擎") {
                TextField("python3 路徑（留空自動搵）", text: $pythonPath)
                TextField("sessionsync.py 路徑（留空用 app 內置）", text: $enginePath)
                LabeledContent("目前使用引擎", value: Engine.shared.scriptURL.path)
                    .font(.caption)
                LabeledContent("目前使用 Python", value: Engine.shared.pythonPath)
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .padding()
    }
}
