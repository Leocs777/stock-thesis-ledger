import SwiftUI

func labLocalized(_ source: String) -> String {
    guard UserDefaults.standard.string(forKey: "appLanguage") ?? "zh-Hans" != "en",
          let path = Bundle.main.path(forResource: "zh-Hans", ofType: "lproj"),
          let bundle = Bundle(path: path)
    else { return source }
    let translated = bundle.localizedString(forKey: source, value: source, table: nil)
    if translated != source { return translated }

    if source.hasSuffix(" symbols"), let count = source.split(separator: " ").first {
        return "\(count) 个标的"
    }
    if source.hasSuffix(" bars"), let count = source.split(separator: " ").first {
        return "\(count) 根 K 线"
    }
    if source.hasSuffix(" positions"), let count = source.split(separator: " ").first {
        return "\(count) 个持仓"
    }
    if source.hasSuffix(" trades"), let count = source.split(separator: " ").first {
        return "\(count) 笔交易"
    }
    if source.hasSuffix(" decisions"), let count = source.split(separator: " ").first {
        return "\(count) 个决策"
    }
    if source.hasSuffix(" trading days"), let count = source.split(separator: " ").first {
        return "\(count) 个交易日"
    }
    if source.hasSuffix(" shares"), let count = source.split(separator: " ").first {
        return "\(count) 股"
    }
    if source.hasSuffix(" units"), let count = source.split(separator: " ").first {
        return "\(count) 单位"
    }
    if source.hasSuffix(" open positions"), let count = source.split(separator: " ").first {
        return "\(count) 个未平仓持仓"
    }
    if source.contains(" configured; add "), source.hasSuffix(" more liquid symbols.") {
        return source
            .replacingOccurrences(of: " configured; add ", with: " 个已配置；还需添加 ")
            .replacingOccurrences(of: " more liquid symbols.", with: " 个流动性良好的标的。")
    }
    if source.hasSuffix(" saved plan(s) have no followed/skipped decision."),
       let count = source.split(separator: " ").first {
        return "\(count) 个已保存计划尚未记录执行或跳过决定。"
    }
    if source.hasSuffix(" saved plan(s) need a followed/skipped choice or outcome review."),
       let count = source.split(separator: " ").first {
        return "\(count) 个已保存计划需要记录执行/跳过决定或结果复盘。"
    }
    if source.hasPrefix("Validation coverage has "), source.hasSuffix(" of 5 required symbols.") {
        return source
            .replacingOccurrences(of: "Validation coverage has ", with: "验证覆盖已有 ")
            .replacingOccurrences(of: " of 5 required symbols.", with: " 个标的，共需要 5 个。")
    }
    if source.hasSuffix(" annual periods"), let count = source.split(separator: " ").first {
        return "\(count) 个年度期间"
    }
    if source.hasPrefix("Enabled · "), source.hasSuffix(" recent filings tracked") {
        return source
            .replacingOccurrences(of: "Enabled · ", with: "已启用 · 正在跟踪 ")
            .replacingOccurrences(of: " recent filings tracked", with: " 份近期申报")
    }
    if source.contains(" new SEC filing(s) and "), source.hasSuffix(" changed annual metric(s).") {
        return source
            .replacingOccurrences(of: " new SEC filing(s) and ", with: " 份新 SEC 申报，")
            .replacingOccurrences(of: " changed annual metric(s).", with: " 项年度指标发生变化。")
    }
    if source.contains(" filed "), source.contains("; report period ") {
        let filingParts = source.components(separatedBy: " filed ")
        let periodParts = filingParts.last?.components(separatedBy: "; report period ") ?? []
        if filingParts.count == 2, periodParts.count == 2 {
            let period = periodParts[1].replacingOccurrences(of: ".", with: "")
            return "\(filingParts[0]) 于 \(periodParts[0]) 提交；报告期 \(period == "not supplied" ? "未提供" : period)。"
        }
    }
    for (prefix, translatedPrefix) in [
        ("Revenue growth ", "营收增长 "),
        ("Net margin ", "净利率 "),
        ("Operating cash flow ", "经营现金流 "),
        ("Liabilities ", "负债 "),
        ("Shareholders' equity ", "股东权益 "),
    ] where source.hasPrefix(prefix) {
        return source.replacingOccurrences(of: prefix, with: translatedPrefix)
    }
    for (label, translatedLabel) in [
        ("Fundamental growth", "基本面成长"),
        ("Profitability", "盈利能力"),
        ("Free cash flow quality", "自由现金流质量"),
        ("Balance-sheet resilience", "资产负债表韧性"),
        ("Earnings valuation", "盈利估值"),
        ("Dividend income", "股息收益"),
    ] where source.hasPrefix("\(label): ") {
        return "\(translatedLabel)：\(labLocalized(String(source.dropFirst(label.count + 2))))"
    }
    if source.hasPrefix("Revenue "), source.contains(" · Net income ") {
        return source
            .replacingOccurrences(of: "Revenue ", with: "营收 ")
            .replacingOccurrences(of: " · Net income ", with: " · 净利润 ")
            .replacingOccurrences(of: "unavailable", with: "不可用")
    }
    if source.hasPrefix("SEC factor coverage is "), source.hasSuffix("% for this strategy.") {
        return source
            .replacingOccurrences(of: "SEC factor coverage is ", with: "此策略的 SEC 因子覆盖率为 ")
            .replacingOccurrences(of: "% for this strategy.", with: "%。")
    }
    for (prefix, translatedPrefix) in [
        ("Net margin ", "净利率 "),
        ("FCF margin ", "自由现金流率 "),
        ("Liabilities / assets ", "负债 / 资产 "),
        ("Price / diluted EPS ", "市价 / 稀释每股收益 "),
        ("Indicated annual yield ", "申报年化股息率 "),
    ] where source.hasPrefix(prefix) {
        return source
            .replacingOccurrences(of: prefix, with: translatedPrefix)
            .replacingOccurrences(of: "unavailable", with: "不可用")
    }
    if source.hasSuffix(" days remaining"), let count = source.split(separator: " ").first {
        return "剩余 \(count) 天"
    }
    if source.hasSuffix(" days past expiration"), let count = source.split(separator: " ").first {
        return "已过期 \(count) 天"
    }
    if source.hasPrefix("Score "), source.contains("/100; position is ") {
        return source
            .replacingOccurrences(of: "Score ", with: "评分 ")
            .replacingOccurrences(of: "; position is ", with: "；仓位占模拟账户 ")
            .replacingOccurrences(of: "% of paper account.", with: "%。")
    }
    if source.hasPrefix("Score "), source.contains("/100 with ") {
        return source
            .replacingOccurrences(of: "Score ", with: "评分 ")
            .replacingOccurrences(of: "/100 with ", with: "/100，")
    }
    if source.hasPrefix("Latest bar is ") {
        return source
            .replacingOccurrences(of: "Latest bar is ", with: "最新 K 线已过 ")
            .replacingOccurrences(of: " calendar days old.", with: " 个日历日。")
    }
    if source.hasPrefix("Changed from ") {
        return source
            .replacingOccurrences(of: "Changed from ", with: "信号从「")
            .replacingOccurrences(of: " to ", with: "」变为「")
            .replacingOccurrences(of: ".", with: "」。")
    }
    if source.hasPrefix("Signal unchanged; score moved ") {
        return source
            .replacingOccurrences(of: "Signal unchanged; score moved ", with: "信号未变；评分变化 ")
            .replacingOccurrences(of: " points.", with: " 分。")
    }
    if source.contains(" attention item(s), "), source.hasSuffix(" opportunity candidate(s).") {
        return source
            .replacingOccurrences(of: " attention item(s), ", with: " 个需关注项，")
            .replacingOccurrences(of: " opportunity candidate(s).", with: " 个机会候选。")
    }
    if source.hasPrefix("20-day momentum is ") {
        return source.replacingOccurrences(of: "20-day momentum is ", with: "20 日动量为 ")
    }
    if source.hasPrefix("Latest volume is ") {
        return source
            .replacingOccurrences(of: "Latest volume is ", with: "最新成交量为 20 日均量的 ")
            .replacingOccurrences(of: "% of its 20-day average.", with: "%。")
    }
    if source.hasPrefix("Latest trading date is ") {
        return source
            .replacingOccurrences(of: "Latest trading date is ", with: "最新交易日已过 ")
            .replacingOccurrences(of: " calendar days old.", with: " 个日历日。")
    }
    if source.hasPrefix("60 daily bars are required; ") {
        return source
            .replacingOccurrences(of: "60 daily bars are required; ", with: "需要 60 根日线；当前已缓存 ")
            .replacingOccurrences(of: " are cached.", with: " 根。")
    }
    if source.contains("% of paper account vs "), source.hasSuffix("% cap") {
        return source
            .replacingOccurrences(of: "% of paper account vs ", with: "% 模拟账户，上限 ")
    }
    if source.hasSuffix("% of account held") {
        return source.replacingOccurrences(of: "% of account held", with: "% 账户已持仓")
    }
    if source.hasSuffix(" cash") {
        return source.replacingOccurrences(of: " cash", with: " 现金")
    }
    if source.hasPrefix("Drawdown ") {
        return source.replacingOccurrences(of: "Drawdown ", with: "回撤 ")
    }
    if source.hasPrefix("Latest volume "), source.hasSuffix("Descriptive history only.") {
        return source
            .replacingOccurrences(of: "Latest volume ", with: "最新成交量 ")
            .replacingOccurrences(of: " of the ", with: "，平均量 ")
            .replacingOccurrences(of: " average. Descriptive history only.", with: "。仅作历史描述。")
    }
    if source.hasPrefix("Historical scenario: ") {
        return source
            .replacingOccurrences(of: "Historical scenario: ", with: "历史情景：")
            .replacingOccurrences(of: " vs buy-and-hold ", with: "，买入并持有 ")
            .replacingOccurrences(of: "; max drawdown ", with: "；最大回撤 ")
    }
    return source
}

enum LabTokens {
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
    }

    enum Radius {
        static let small: CGFloat = 11
        static let medium: CGFloat = 17
        static let large: CGFloat = 24
    }
}

enum LabTone {
    case neutral
    case positive
    case warning
    case negative
    case blocked

    var foreground: Color {
        switch self {
        case .neutral: .secondary
        case .positive: .labGreen
        case .warning: .labSignalInk
        case .negative: .labNegative
        case .blocked: .labInkSoft
        }
    }

    var background: Color {
        switch self {
        case .neutral: Color.clear
        case .positive: Color.labGreen.opacity(0.08)
        case .warning: .labSignalSoft
        case .negative: Color.labNegative.opacity(0.08)
        case .blocked: Color.labInk.opacity(0.07)
        }
    }

    var border: Color {
        switch self {
        case .neutral, .blocked: .labLine
        case .positive: Color.labGreen.opacity(0.26)
        case .warning: Color.signalOrange.opacity(0.30)
        case .negative: Color.labNegative.opacity(0.26)
        }
    }
}

enum LabButtonVariant {
    case primary
    case secondary
    case quiet
    case destructive
}

struct LabButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    var variant: LabButtonVariant = .primary
    var compact = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.bold))
            .frame(maxWidth: compact ? nil : .infinity)
            .frame(minHeight: compact ? 36 : 44)
            .padding(.horizontal, compact ? 12 : 15)
            .foregroundStyle(foreground)
            .background(background(configuration: configuration))
            .overlay(
                RoundedRectangle(cornerRadius: LabTokens.Radius.small, style: .continuous)
                    .stroke(border, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: LabTokens.Radius.small, style: .continuous))
            .opacity(isEnabled ? 1 : 0.46)
            .scaleEffect(configuration.isPressed && isEnabled ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }

    private var foreground: Color {
        switch variant {
        case .primary, .destructive: .white
        case .secondary, .quiet: .labInk
        }
    }

    private var border: Color {
        switch variant {
        case .secondary: .labLine
        default: .clear
        }
    }

    private func background(configuration: Configuration) -> Color {
        let pressed = configuration.isPressed && isEnabled
        switch variant {
        case .primary: return pressed ? Color.signalOrange : Color.labInk
        case .secondary: return pressed ? Color.labSignalSoft : Color.labCard
        case .quiet: return pressed ? Color.labInk.opacity(0.07) : Color.clear
        case .destructive: return pressed ? Color.labNegative.opacity(0.84) : Color.labNegative
        }
    }
}

struct LabBadge: View {
    let text: String
    var tone: LabTone = .neutral
    var showsIndicator = true

    init(_ text: String, tone: LabTone = .neutral, showsIndicator: Bool = true) {
        self.text = text
        self.tone = tone
        self.showsIndicator = showsIndicator
    }

    var body: some View {
        HStack(spacing: 6) {
            if showsIndicator {
                Circle().fill(tone.foreground).frame(width: 6, height: 6)
            }
            Text(labLocalized(text))
                .textCase(.uppercase)
                .font(.system(size: 11, weight: .bold))
                .tracking(0.7)
        }
        .foregroundStyle(tone.foreground)
        .padding(.horizontal, 8)
        .frame(minHeight: 28)
        .background(tone.background, in: Capsule())
        .overlay(Capsule().stroke(tone.border))
        .accessibilityElement(children: .combine)
    }
}

struct LabMetricCard: View {
    let label: String
    let value: String
    let detail: String
    var tone: LabTone = .neutral

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(labLocalized(label))
                .textCase(.uppercase)
                .font(.system(size: 11, weight: .bold))
                .tracking(0.8)
                .foregroundStyle(.secondary)
            Text(labLocalized(value))
                .font(.title3.monospacedDigit().weight(.bold))
                .foregroundStyle(tone == .neutral ? Color.labInk : tone.foreground)
            Text(labLocalized(detail)).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 86, alignment: .leading)
        .padding(15)
        .background(Color.labCard, in: RoundedRectangle(cornerRadius: LabTokens.Radius.medium, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: LabTokens.Radius.medium, style: .continuous).stroke(Color.labLine))
        .accessibilityElement(children: .combine)
    }
}

struct LabSection<Content: View>: View {
    let title: String
    let subtitle: String
    let badge: String?
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(labLocalized(title)).font(.title2.weight(.semibold))
                    Text(labLocalized(subtitle)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let badge { LabBadge(badge, tone: .blocked, showsIndicator: false) }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(Color.labCard, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Color.labLine))
    }
}

struct LabGuardrailCard: View {
    let eyebrow: String
    let title: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            Text(labLocalized(eyebrow)).font(.caption.weight(.bold)).tracking(1.2).foregroundStyle(Color.labSignalInk)
            Text(labLocalized(title)).font(.system(size: 31, weight: .semibold)).tracking(-0.8)
            Text(labLocalized(message)).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Label("Paper mode / no broker execution", systemImage: "lock.shield.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.labSignalInk)
                .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(21)
        .background(Color.labSignalSoft, in: RoundedRectangle(cornerRadius: LabTokens.Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: LabTokens.Radius.large, style: .continuous).stroke(Color.signalOrange.opacity(0.22)))
        .accessibilityElement(children: .combine)
    }
}

struct LabReadinessRow: View {
    let icon: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).frame(width: 22).foregroundStyle(Color.signalOrange)
            VStack(alignment: .leading, spacing: 3) {
                Text(labLocalized(title)).font(.subheadline.weight(.semibold))
                Text(labLocalized(detail)).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

struct LabEmptyLine: View {
    let text: String
    var body: some View { Text(labLocalized(text)).font(.subheadline).foregroundStyle(.secondary) }
}

extension Color {
    static let labPaper = Color(red: 0.95, green: 0.94, blue: 0.90)
    static let labCard = Color(red: 0.99, green: 0.98, blue: 0.95)
    static let labInk = Color(red: 0.08, green: 0.13, blue: 0.12)
    static let labInkSoft = Color(red: 0.15, green: 0.21, blue: 0.19)
    static let signalOrange = Color(red: 0.91, green: 0.36, blue: 0.16)
    static let labSignalInk = Color(red: 0.59, green: 0.22, blue: 0.09)
    static let labSignalSoft = Color(red: 0.98, green: 0.86, blue: 0.81)
    static let labGreen = Color(red: 0.05, green: 0.47, blue: 0.40)
    static let labMint = Color(red: 0.40, green: 0.84, blue: 0.70)
    static let labNegative = Color(red: 0.70, green: 0.24, blue: 0.18)
    static let labLine = Color.black.opacity(0.09)
}
