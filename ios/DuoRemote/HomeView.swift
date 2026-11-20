import SwiftUI

/// 採用箱の入り口。ホームにモジュールのアイコンが並び、選んだ機能の画面へ入る。
/// 機能を増やすときは、ここにタイルと画面を足す。
struct RootView: View {
    @EnvironmentObject private var session: RemoteSession
    @State private var screen: Screen = .home

    enum Screen {
        case home, remote, keyboard
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            switch screen {
            case .home:
                HomeView(onOpen: { screen = $0 })
            case .remote:
                ContentView()
            case .keyboard:
                KeyboardInfoView()
            }

            // 接続中はリモート操作の邪魔をしないよう、戻るボタンを出さない
            if screen != .home && !session.isConnectedOrReconnecting {
                Button {
                    screen = .home
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.left")
                        Text("ホーム")
                    }
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                }
                .padding(.leading, 16)
                .padding(.top, 8)
                .zIndex(1)
            }
        }
    }
}

private struct HomeView: View {
    var onOpen: (RootView.Screen) -> Void

    private let columns = [GridItem(.adaptive(minimum: 96), spacing: 20)]

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    Text("採用箱")
                        .font(.largeTitle.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.top, 32)

                    LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
                        tile(title: "リモート", systemImage: "display") { onOpen(.remote) }
                        tile(title: "キーボード", systemImage: "keyboard") { onOpen(.keyboard) }
                    }
                }
                .padding(.horizontal, 24)
            }
        }
    }

    private func tile(title: String, systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 10) {
                Image(systemName: systemImage)
                    .font(.system(size: 30))
                    .frame(width: 68, height: 68)
                    .background(Color.white.opacity(0.1), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                Text(title)
                    .font(.callout)
            }
            .foregroundStyle(.white)
        }
    }
}

/// 同梱キーボード（DuoFlick）の案内。キーボード自体の設定は、キーボード内の設定画面で行う。
private struct KeyboardInfoView: View {
    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("キーボード（DuoFlick）")
                        .font(.title2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.top, 56)

                    Text("このアプリに、DuoFlick のキーボードが入っています。使うには、iPhone の設定でキーボードを追加してください。")
                        .foregroundStyle(.white.opacity(0.85))

                    VStack(alignment: .leading, spacing: 10) {
                        step("1", "設定アプリを開く")
                        step("2", "一般 → キーボード → キーボード の順に進む")
                        step("3", "「新しいキーボードを追加」から DuoFlick を選ぶ")
                        step("4", "追加した DuoFlick を開いて「フルアクセスを許可」をオンにする（単語登録や設定の保存に必要です）")
                    }

                    Text("レイアウトや変換の調整は、キーボードを出して、その中にある設定画面で行います。")
                        .foregroundStyle(.white.opacity(0.85))

                    Button {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    } label: {
                        Text("設定アプリを開く")
                            .font(.callout.weight(.medium))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 10)
                            .background(.ultraThinMaterial, in: Capsule())
                            .foregroundStyle(.white)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 24)
                .padding(.bottom, 32)
            }
        }
    }

    private func step(_ number: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(number)
                .font(.callout.weight(.bold))
                .frame(width: 24, height: 24)
                .background(Color.white.opacity(0.15), in: Circle())
            Text(text)
                .foregroundStyle(.white.opacity(0.9))
        }
    }
}
