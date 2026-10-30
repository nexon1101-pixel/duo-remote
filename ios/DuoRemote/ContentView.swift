import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var session: RemoteSession

    // 接続先は端末に覚えておく。トークンの保管（キーチェーン）は後で対応する
    @AppStorage("host") private var host = ""
    @AppStorage("port") private var port = "8765"
    @AppStorage("token") private var token = ""

    @State private var showingWindows = false
    /// 映像を出している範囲の大きさ（ポイント）。ウィンドウの大きさを合わせるときに使う
    @State private var screenSize: CGSize = .zero

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            switch session.state {
            case .connected:
                connectedView
            default:
                connectForm
            }
        }
    }

    private var connectedView: some View {
        GeometryReader { proxy in
            VideoView(renderer: session.renderer, videoSize: session.videoSize, send: session.send)
                .onAppear { screenSize = proxy.size }
                .onChange(of: proxy.size) { _, size in screenSize = size }
        }
        .ignoresSafeArea()
        .overlay(alignment: .topTrailing) { menu }
        .overlay(alignment: .top) {
            if let notice = session.notice {
                Text(notice)
                    .font(.callout)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.top, 8)
                    .padding(.horizontal, 60)
                    .transition(.opacity)
            }
        }
        .animation(.default, value: session.notice)
        .sheet(isPresented: $showingWindows) {
            WindowPicker(fitSize: screenSize)
        }
    }

    private var menu: some View {
        Menu {
            if let window = session.currentWindow {
                Text("表示中: \(window.title)")
            }
            Button("ウィンドウを選ぶ", systemImage: "macwindow") {
                session.requestWindows()
                showingWindows = true
            }
            if session.currentWindow != nil {
                Button("画面全体を表示", systemImage: "display") { session.showScreen() }
            }
            Button("切断", systemImage: "xmark.circle", role: .destructive) { session.disconnect() }
        } label: {
            Image(systemName: "ellipsis.circle.fill")
                .font(.title)
                .symbolRenderingMode(.palette)
                .foregroundStyle(.white, .black.opacity(0.5))
                .padding(12)
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
