import Foundation

// MARK: - Flexible JSON primitive (for JSONB fields with mixed types)

enum JSONPrimitive: Codable, CustomStringConvertible {
    case string(String), double(Double), int(Int), bool(Bool), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let v = try? c.decode(Bool.self)   { self = .bool(v);   return }
        if let v = try? c.decode(Int.self)    { self = .int(v);    return }
        if let v = try? c.decode(Double.self) { self = .double(v); return }
        if let v = try? c.decode(String.self) { self = .string(v); return }
        self = .null
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .double(let v): try c.encode(v)
        case .int(let v):    try c.encode(v)
        case .bool(let v):   try c.encode(v)
        case .null:          try c.encodeNil()
        }
    }

    var description: String {
        switch self {
        case .string(let v): return v
        case .double(let v): return String(format: "%.2f", v)
        case .int(let v):    return "\(v)"
        case .bool(let v):   return v ? "true" : "false"
        case .null:          return "—"
        }
    }
}

// MARK: - PM Agent

struct PMStatus: Codable {
    let mode: String
    let cycleIntervalSeconds: Int
    let dailyLossHaltTriggered: Bool
    let haltedUntil: String?
    let portfolio: PortfolioSummary
    let lastCycle: LastCycleInfo?
    let decisionsToday: Int
    let activeCriticalAlerts: Int
}

struct PortfolioSummary: Codable {
    let positionCount: Int
    let grossExposure: Double
    let netExposure: Double
    let cashPct: Double
}

struct LastCycleInfo: Codable {
    let timestamp: String?
    let executionStatus: String?
    let category: String?
    let decision: String?
}

// MARK: - PM Decisions

struct PMDecision: Identifiable, Codable {
    // db `id` is SERIAL/Int — omit from Codable, use decisionId as Identifiable key
    var id: String { decisionId }
    let decisionId: String
    let timestamp: String
    let category: String
    let ticker: String?
    let decision: String
    let reasoning: String
    let riskAssessment: String?
    let confidence: Double?
    let executionStatus: String
    let actionDetails: [String: JSONPrimitive]?
    let humanOverride: [String: JSONPrimitive]?
    let createdAt: String?
}

// MARK: - Portfolio / Positions

struct Position: Identifiable, Codable {
    let id: String
    let ticker: String
    let direction: String
    let status: String
    let convictionScore: Double
    let kellyFraction: Double
    let dollarSize: Double
    let shareCount: Double
    let sizeLabel: String
    let pctOfPortfolio: Double
    let entryPrice: Double?
    let currentPrice: Double?
    let stopLossPrice: Double?
    let targetPrice: Double?
    let riskRewardRatio: Double?
    let sizingRationale: String?
    let sector: String?
    let regimeAtSizing: String?
    let pnl: Double?
    let pnlPct: Double?
    let openedAt: String?
    let closedAt: String?
    let createdAt: String?
    let exitAction: String?
    let exitTrimPct: Double?
    let stopTier1: Double?
    let stopTier2: Double?
    let stopTier3: Double?
    let nextEarningsDate: String?
}

struct ExposureReport: Codable {
    let regime: String
    let grossExposurePct: Double
    let netExposurePct: Double
    let positionCount: Int
    let sectorConcentration: [String: Double]
    let caps: ExposureCaps
}

struct ExposureCaps: Codable {
    let maxGross: Double
    let maxNetLong: Double
    let maxNetShort: Double
}

// MARK: - Risk

struct RiskAlert: Identifiable, Codable {
    let id: String
    let ticker: String?
    let tier: Int
    let severity: String
    let trigger: String
    let regime: String
    let resolved: Bool
    let resolvedAt: String?
    let createdAt: String?
}

struct PortfolioMetrics: Codable {
    let date: String?
    let sharpeRatio: Double?
    let sortinoRatio: Double?
    let maxDrawdown: Double?
    let var95: Double?
    let var99: Double?
    let beta: Double?
    let calmarRatio: Double?
    let grossExposure: Double?
    let netExposure: Double?
}

struct RiskStatus: Codable {
    let activeAlertsCount: Int
    let criticalCount: Int
    let warnCount: Int
    let breachCount: Int
    let latestMetricsDate: String?
    let autonomousModeSuspended: Bool
    let regime: String?
}

// MARK: - Macro

struct MacroBriefing: Codable {
    let date: String
    let regime: String
    let regimeScore: Double
    let overrideFlag: Bool
    let qualitativeSummary: String
    let keyThemes: [String]
    let portfolioGuidance: String
    let regimeChanged: Bool
    let previousRegime: String?
    let growthScore: Double?
    let inflationScore: Double?
    let fedScore: Double?
    let stressScore: Double?
    let regimeConfidence: Double?
    let indicatorScores: [IndicatorScore]?
}

struct IndicatorScore: Codable, Identifiable {
    var id: String { name }
    let name: String
    let value: Double
    let signal: String
    let note: String?
}

// MARK: - Screener Watchlist

struct WatchlistItem: Identifiable, Codable {
    let id: String
    let ticker: String
    let compositeScore: Double
    let qualityScore: Double
    let valueScore: Double
    let momentumScore: Double
    let rank: Int
    let runDate: String?
    let sector: String?
    let marketCapM: Double?
    let advK: Double?
    let regime: String?
    let beneishMScore: Double?
    let beneishFlag: String?
    let insiderSignal: Bool?
    let queuedForResearch: Bool?
}

// MARK: - Research Memos

struct ResearchMemo: Identifiable, Codable {
    let id: String
    let ticker: String
    let date: String?
    let verdict: String
    let convictionScore: Double
    let status: String
    let deferredUntil: String?
    let createdAt: String?
    let memoJson: MemoContent?
}

struct MemoContent: Codable {
    let summary: String?
    let companyOverview: String?
    let variantPerception: String?
    let repricingCatalyst: String?
    let bullThesis: [String]?
    let bearThesis: [String]?
    let keyRisks: [String]?
    let catalysts: [String]?
    let priceTarget: Double?
    let convictionScoreRationale: String?
    let valuationNote: String?
    let macroSensitivity: String?
    let sector: String?
}

// MARK: - Orders

struct Order: Identifiable, Codable {
    let id: String
    let positionId: String?
    let ticker: String
    let direction: String
    let orderType: String
    let requestedQty: Double
    let limitPrice: Double?
    let ibkrOrderId: Int?
    let status: String
    let totalFilledQty: Double
    let avgFillPrice: Double?
    let submittedAt: String?
    let filledAt: String?
    let orderSide: String
    let exitType: String?
    let createdAt: String?
}
