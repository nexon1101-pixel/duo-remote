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
