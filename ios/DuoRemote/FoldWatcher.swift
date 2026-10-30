import SwiftUI
import UIKit

/// iPhone Duo の開き具合と、折り目の位置。
/// iOS 27.1 の UIHingeInteraction と reservedRegions を使う。それより前の iOS や、折りたためない端末では、unknown のまま。
struct FoldState: Equatable {
    enum Posture: Equatable {
        case unknown
        case closed
        /// 半開き（ノートPCのような形）
        case partiallyOpen
        case fullyOpen
    }

    var posture: Posture = .unknown
    /// 折り目の位置（この部品を置いた View の座標）。なければ nil
    var fold: CGRect?

    /// 半開きで、折り目が横に走っている（上と下に分かれている）
    var isTabletop: Bool {
        guard posture == .partiallyOpen, let fold else { return false }
        return fold.width > fold.height
    }
}

/// 折り目と開き具合を見る、見えない部品。画面いっぱいに、背景として置く
struct FoldWatcher: UIViewRepresentable {
    @Binding var state: FoldState

    func makeUIView(context: Context) -> FoldProbeView {
        let view = FoldProbeView()
        view.onChange = { new in
            if state != new { state = new }
        }
        return view
    }

    func updateUIView(_ uiView: FoldProbeView, context: Context) {
        uiView.onChange = { new in
            if state != new { state = new }
        }
    }
}

final class FoldProbeView: UIView {
    var onChange: ((FoldState) -> Void)?
    private var posture: FoldState.Posture = .unknown
    private var fold: CGRect?

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        backgroundColor = .clear
        if #available(iOS 27.1, *) {
            addInteraction(UIHingeInteraction { [weak self] _, update in
                guard let self else { return }
                if let hinge = update.hinge {
                    switch hinge.status {
                    case .closed: self.posture = .closed
                    case .partiallyOpen: self.posture = .partiallyOpen
                    case .fullyOpen: self.posture = .fullyOpen
                    default: self.posture = .unknown
                    }
                } else {
                    self.posture = .unknown
                }
                self.publish()
            })
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override class var requiresConstraintBasedLayout: Bool { true }

    // UIKit は、updateConstraints() の中で読んだ reservedRegions を覚えていて、
    // 折り目が動いたときに、もう一度 updateConstraints() を呼ぶ
    override func updateConstraints() {
        readRegions()
        super.updateConstraints()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        setNeedsUpdateConstraints()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        setNeedsUpdateConstraints()
    }

    private func readRegions() {
        var found: CGRect?
        if #available(iOS 27.1, *) {
            found = reservedRegions(kind: .division, options: .includeInactive).first?.frame
        }
        if found != fold {
            fold = found
            publish()
        }
    }

    private func publish() {
        let state = FoldState(posture: posture, fold: fold)
        DispatchQueue.main.async { [weak self] in
            self?.onChange?(state)
        }
    }
}
