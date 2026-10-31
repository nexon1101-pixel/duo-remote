import Foundation

/// 画質を自動で上げ下げする決まり。
/// サーバーの stats（約2秒ごと）と、ping の往復の時間を見る。決まりはアプリが持ち、サーバーは言われたとおりにする。
/// - 映像が捨てられた（追いつかなかった）か、遅れが大きい、が2回続いたら、1段下げる
/// - どちらもない、が30秒続いたら、1段上げる。利用者が選んだ画質（ceiling）より上には上げない
struct AutoQuality {
    /// 高い順
    static let levels: [VideoQuality] = [.high, .medium, .low]
    static let badLatencyMs = 300
    static let goodLatencyMs = 150
    static let badInARow = 2
    static let stableSeconds: TimeInterval = 30

    let ceiling: VideoQuality
    private(set) var current: VideoQuality
    private var badCount = 0
    private var goodSince: Date?

    init(ceiling: VideoQuality, current: VideoQuality? = nil) {
        self.ceiling = ceiling
        // 利用者が選んだ画質より上からは始めない
        let ceilingIndex = Self.levels.firstIndex(of: ceiling) ?? 0
        let currentIndex = current.flatMap { Self.levels.firstIndex(of: $0) } ?? ceilingIndex
        self.current = Self.levels[max(currentIndex, ceilingIndex)]
    }

    /// stats が届いたときに呼ぶ。画質を変えるときだけ、新しい画質を返す
    mutating func update(dropped: Int, latencyMs: Int?, now: Date = Date()) -> VideoQuality? {
        let latency = latencyMs ?? 0
        let index = Self.levels.firstIndex(of: current) ?? 0
        if dropped > 0 || latency > Self.badLatencyMs {
            badCount += 1
            goodSince = nil
            guard badCount >= Self.badInARow, index + 1 < Self.levels.count else { return nil }
            badCount = 0
            current = Self.levels[index + 1]
            return current
        }
        badCount = 0
        guard latency < Self.goodLatencyMs else {
            goodSince = nil
            return nil
        }
        let since = goodSince ?? now
        goodSince = since
        let ceilingIndex = Self.levels.firstIndex(of: ceiling) ?? 0
        guard index > ceilingIndex, now.timeIntervalSince(since) >= Self.stableSeconds else { return nil }
        goodSince = now // 上げたあとも、また30秒見てから次を決める
        current = Self.levels[index - 1]
        return current
    }
}
