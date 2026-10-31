import SwiftUI

/// 映像の上に重ねる部品の見た目をそろえる。
/// iOS 26 以降は Liquid Glass、それより前は、すりガラス（material）にする。
extension View {
    /// ガラスの背景。tint を渡すと、その色を薄く付ける
    @ViewBuilder
    func glass<S: Shape>(in shape: S, tint: Color? = nil, interactive: Bool = false) -> some View {
        if #available(iOS 26.0, *) {
            self.glassEffect(Self.makeGlass(tint: tint, interactive: interactive), in: shape)
        } else {
            self
                .background {
                    ZStack {
                        shape.fill(.ultraThinMaterial)
                        if let tint { shape.fill(tint.opacity(0.35)) }
                    }
                }
                .overlay { shape.stroke(.white.opacity(0.15), lineWidth: 0.5) }
        }
    }

    @available(iOS 26.0, *)
    private static func makeGlass(tint: Color?, interactive: Bool) -> Glass {
        var glass = Glass.regular
        if let tint { glass = glass.tint(tint) }
        if interactive { glass = glass.interactive() }
        return glass
    }

    /// 丸いガラスのボタンにする（メニュー、閉じる、など）
    func glassCircle(size: CGFloat = 44) -> some View {
        self
            .font(.system(size: size * 0.4, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .glass(in: Circle(), interactive: true)
            .contentShape(Circle())
    }
}

/// ガラスのボタン。iOS 26 以降は .glass / .glassProminent、それより前は bordered にする
struct GlassButtonStyleModifier: ViewModifier {
    var prominent = false

    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if prominent {
                content.buttonStyle(.glassProminent)
            } else {
                content.buttonStyle(.glass)
            }
        } else {
            if prominent {
                content.buttonStyle(.borderedProminent)
            } else {
                content.buttonStyle(.bordered)
            }
        }
    }
}

extension View {
    func glassButton(prominent: Bool = false) -> some View {
        modifier(GlassButtonStyleModifier(prominent: prominent))
    }
}

/// 遅れの数字の色。少ないほど緑
enum LatencyColor {
    static func color(ms: Int) -> Color {
        switch ms {
        case ..<60: .green
        case ..<150: .yellow
        default: .red
        }
    }
}

/// 画面の部品の大きさと間隔。ボタンの高さや間隔は、ここで決めて、どの画面でもそろえる
enum UIMetrics {
    /// 丸いボタン（右下のウィンドウ、トラックパッド、キーボード、右上の「…」）の高さ
    static let buttonHeight: CGFloat = 44
    /// 半開きの、下の画面の帯に置く、背の低いボタンの高さ
    static let compactButtonHeight: CGFloat = 34
    /// ボタンとボタンの間隔
    static let spacing: CGFloat = 10
    /// 画面の端からの余白
    static let edgePadding: CGFloat = 12
    /// フロートのキーボードとトラックパッドの、上の帯の高さ
    static let barHeight: CGFloat = 32
    /// 帯の中の、丸いボタンの絵の大きさと、押せる幅
    static let barIconSize: CGFloat = 26
    static let barIconHitWidth: CGFloat = 44
    /// 板（キーボードやトラックパッドの背景）の角の丸み
    static let panelRadius: CGFloat = 20
}

/// 帯の中の、小さい丸いボタン（閉じる、下部に固定、フロートにする）。見た目は小さく、押せる範囲は 44 以上にする
struct BarIconButton: View {
    let systemImage: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: UIMetrics.barIconSize, height: UIMetrics.barIconSize)
                .background(Color.white.opacity(0.15), in: Circle())
                .frame(width: UIMetrics.barIconHitWidth, height: UIMetrics.barHeight)
                .contentShape(Rectangle())
        }
        .accessibilityLabel(label)
    }
}
