import SwiftUI

/// 映像の上に重ねて出すトラックパッド。アプリのキーボード（OverlayKeyboard）と同じく、動かせる、大きさを変えられる、透かせる。
/// 上の帯を引くと動き、帯の右端のつまみを引くと大きさが変わる。パッドの外は、下の映像に触れる。
/// 左右のボタンは置かない（タップで左クリック、2本指のタップで右クリック。TrackpadPad を見る）。
struct OverlayTrackpad: View {
    let session: RemoteSession
    /// 閉じるボタンを押したとき
    var onClose: () -> Void

    /// 濃さは、アプリのキーボードと同じ設定を使う
    @AppStorage(OverlayKeyboard.opacityKey) private var opacity = 0.8
    /// 大きさ（ポイント）。0 のときは、初めの大きさ
    @AppStorage("overlayTrackpadWidth") private var savedWidth = 0.0
    @AppStorage("overlayTrackpadHeight") private var savedHeight = 0.0
    /// 真ん中の位置（画面の幅・高さを1とした割合）
    @AppStorage("overlayTrackpadX") private var savedX = 0.75
    @AppStorage("overlayTrackpadY") private var savedY = 0.7

    /// 動かしている途中・大きさを変えている途中の値
    @State private var dragStart: CGPoint?
    @State private var resizeStart: CGSize?

    private static let barHeight: CGFloat = 26
    private static let minSize = CGSize(width: 150, height: 110)
    private static let initialSize = CGSize(width: 280, height: 190)
    /// 帯と枠の余白（キーボードと同じ）
    private static let sideInset: CGFloat = 4

    var body: some View {
        GeometryReader { proxy in
            let area = proxy.size
            let size = clampedSize(in: area)
            let center = clampedCenter(size: size, in: area)

            VStack(spacing: 0) {
                bar(size: size, area: area)
                TrackpadPad(send: session.send)
                    .padding(.horizontal, 6)
                    .padding(.bottom, 6)
            }
            .padding(.horizontal, Self.sideInset)
            .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .opacity(opacity)
            .frame(width: size.width + Self.sideInset * 2, height: size.height)
            .position(center)
        }
    }

    /// 上の帯。引くと動く。閉じるボタンと、大きさを変えるつまみ
    private func bar(size: CGSize, area: CGSize) -> some View {
        HStack(spacing: 0) {
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .bold))
                    .frame(width: 22, height: 22)
                    .background(Color.white.opacity(0.15), in: Circle())
                    .frame(width: 40, height: Self.barHeight)
            }
            .accessibilityLabel("トラックパッドを閉じる")
            Spacer()
            // 引いて動かせることが分かるように、シートと同じつまみを出す
            Capsule()
                .fill(Color.white.opacity(0.45))
                .frame(width: 40, height: 5)
            Spacer()
            Image(systemName: "arrow.up.left.and.arrow.down.right")
                .font(.system(size: 11, weight: .bold))
                .frame(width: 22, height: 22)
                .background(Color.white.opacity(0.15), in: Circle())
                .frame(width: 44, height: Self.barHeight)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(coordinateSpace: .global)
                        .onChanged { value in
                            let start = resizeStart ?? size
                            resizeStart = start
                            // 真ん中を中心に広がるので、引いた量の2倍だけ変える
                            savedWidth = Double(min(max(start.width + value.translation.width * 2, Self.minSize.width), area.width))
                            savedHeight = Double(min(max(start.height + value.translation.height * 2, Self.minSize.height), area.height))
                        }
                        .onEnded { _ in resizeStart = nil }
                )
        }
        .foregroundStyle(.white)
        .frame(width: size.width, height: Self.barHeight)
        .contentShape(Rectangle())
        .gesture(
            DragGesture(coordinateSpace: .global)
                .onChanged { value in
                    let start = dragStart ?? clampedCenter(size: size, in: area)
                    dragStart = start
                    savedX = Double((start.x + value.translation.width) / max(area.width, 1))
                    savedY = Double((start.y + value.translation.height) / max(area.height, 1))
                }
                .onEnded { _ in
                    // 画面の外に出たままにならないよう、収まる位置で覚え直す
                    let c = clampedCenter(size: size, in: area)
                    savedX = Double(c.x / max(area.width, 1))
                    savedY = Double(c.y / max(area.height, 1))
                    dragStart = nil
                }
        )
    }

    private func clampedSize(in area: CGSize) -> CGSize {
        let w = savedWidth > 0 ? CGFloat(savedWidth) : Self.initialSize.width
        let h = savedHeight > 0 ? CGFloat(savedHeight) : Self.initialSize.height
        return CGSize(
            width: min(max(w, min(Self.minSize.width, area.width)), area.width),
            height: min(max(h, min(Self.minSize.height, area.height)), area.height)
        )
    }

    private func clampedCenter(size: CGSize, in area: CGSize) -> CGPoint {
        func clamp(_ v: CGFloat, _ half: CGFloat, _ limit: CGFloat) -> CGFloat {
            half * 2 >= limit ? limit / 2 : min(max(v, half), limit - half)
        }
        return CGPoint(
            x: clamp(CGFloat(savedX) * area.width, size.width / 2 + Self.sideInset, area.width),
            y: clamp(CGFloat(savedY) * area.height, size.height / 2, area.height)
        )
    }
}
