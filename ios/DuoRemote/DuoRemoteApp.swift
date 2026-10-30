import SwiftUI

@main
struct DuoRemoteApp: App {
    @StateObject private var session = RemoteSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
                // 映像を見るアプリなので、いつも暗い見た目にする
                .preferredColorScheme(.dark)
        }
    }
}
