import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var session: RemoteSession

    // 接続先は端末に覚えておく。トークンの保管（キーチェーン）は後で対応する
    @AppStorage("host") private var host = ""
    @AppStorage("port") private var port = "8765"
    @AppStorage("token") private var token = ""

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            switch session.state {
            case .connected:
                VideoView(renderer: session.renderer, videoSize: session.videoSize, send: session.send)
                    .ignoresSafeArea()
                    .overlay(alignment: .topTrailing) {
                        Button("切断") { session.disconnect() }
                            .buttonStyle(.borderedProminent)
                            .padding()
                    }
            default:
                connectForm
            }
        }
    }

    private var connectForm: some View {
        Form {
            Section("接続先") {
                TextField("ホスト（例: 100.x.x.x）", text: $host)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                TextField("ポート", text: $port)
                    .keyboardType(.numberPad)
                SecureField("トークン", text: $token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            Section {
                Button(session.state == .connecting ? "接続しています…" : "接続") {
                    session.connect(host: host, port: Int(port) ?? 8765, token: token)
                }
                .disabled(host.isEmpty || token.isEmpty || session.state == .connecting)
                if let message = session.errorMessage {
                    Text(message).foregroundStyle(.red)
                }
            }
        }
        .scrollContentBackground(.hidden)
    }
}
