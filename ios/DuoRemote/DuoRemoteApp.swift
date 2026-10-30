import SwiftUI

@main
struct DuoRemoteApp: App {
    @StateObject private var session = RemoteSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
        }
    }
}
