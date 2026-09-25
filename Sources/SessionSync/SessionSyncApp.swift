import SwiftUI

@main
struct SessionSyncApp: App {
    @StateObject private var store = SyncStore()

    var body: some Scene {
        WindowGroup("Codex Claude Session Sync") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 980, minHeight: 560)
                .onAppear { store.refresh(bootstrap: true) }
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("重新掃描") { store.refresh() }.keyboardShortcut("r", modifiers: .command)
                Button("同步全部") { store.syncAll() }.keyboardShortcut("s", modifiers: [.command, .shift])
            }
        }
        Settings {
            SettingsView().environmentObject(store)
        }
    }
}
