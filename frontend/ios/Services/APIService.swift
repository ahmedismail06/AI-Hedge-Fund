import Foundation

enum APIError: LocalizedError {
    case badURL
    case httpError(Int, String)
    case decodingError(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .badURL:                  return "Invalid URL"
        case .httpError(let c, let m): return "HTTP \(c): \(m)"
        case .decodingError(let e):    return "Decode: \(e.localizedDescription)"
        case .networkError(let e):     return e.localizedDescription
        }
    }
}

@MainActor
class APIService {
    static let shared = APIService()

    private let base: String
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    private init() {
        base = AppConfig.API.baseURL
        session = URLSession.shared
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    // MARK: - Core

    func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: base + path) else { throw APIError.badURL }
        do {
            let (data, response) = try await session.data(from: url)
            if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                throw APIError.httpError(http.statusCode, String(data: data, encoding: .utf8) ?? "")
            }
            return try decoder.decode(T.self, from: data)
        } catch let e as APIError   { throw e }
        catch let e as DecodingError { throw APIError.decodingError(e) }
        catch                        { throw APIError.networkError(error) }
    }

    func post(_ path: String, body: Data? = nil) async throws {
        guard let url = URL(string: base + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        let _ = try await session.data(for: req)
    }

    private func encode<T: Encodable>(_ value: T) throws -> Data {
        try encoder.encode(value)
    }

    // MARK: - PM Agent

    func getPMStatus() async throws -> PMStatus { try await fetch("/pm/status") }

    func getDecisions(limit: Int = 50, category: String? = nil, ticker: String? = nil) async throws -> [PMDecision] {
        var path = "/pm/decisions?limit=\(limit)"
        if let cat = category { path += "&category=\(cat)" }
        if let t = ticker     { path += "&ticker=\(t)" }
        return try await fetch(path)
    }

    func overrideDecision(id: String, type: String, reason: String, deferUntil: String? = nil) async throws {
        struct Body: Encodable { let overrideType: String; let reason: String; let deferUntil: String? }
        try await post("/pm/override/\(id)", body: encode(Body(overrideType: type, reason: reason, deferUntil: deferUntil)))
    }

    func haltPM() async throws    { try await post("/pm/override/halt") }
    func resumePM() async throws   { try await post("/pm/override/resume") }
    func triggerCycle() async throws { try await post("/pm/cycle/run") }

    func setMode(_ mode: String) async throws {
        struct Body: Encodable { let mode: String }
        try await post("/pm/config", body: encode(Body(mode: mode)))
    }

    // MARK: - Portfolio

    func getOpenPositions() async throws -> [Position]    { try await fetch("/portfolio/positions") }
    func getPendingPositions() async throws -> [Position] { try await fetch("/portfolio/pending") }
    func getExposure() async throws -> ExposureReport     { try await fetch("/portfolio/exposure") }
    func getPositionHistory(days: Int = 30) async throws -> [Position] {
        try await fetch("/portfolio/history?days=\(days)")
    }
    func approvePosition(_ id: String) async throws { try await post("/portfolio/approve/\(id)") }
    func rejectPosition(_ id: String) async throws  { try await post("/portfolio/reject/\(id)") }
    func forceClose(ticker: String) async throws    { try await post("/pm/override/close/\(ticker.uppercased())") }

    // MARK: - Risk

    func getRiskAlerts() async throws -> [RiskAlert]       { try await fetch("/risk/alerts") }
    func getRiskMetrics() async throws -> PortfolioMetrics  { try await fetch("/risk/metrics") }
    func getRiskStatus() async throws -> RiskStatus         { try await fetch("/risk/status") }
    func resolveAlert(_ id: String) async throws            { try await post("/risk/alerts/\(id)/resolve") }

    // MARK: - Macro

    func getMacroBriefing() async throws -> MacroBriefing { try await fetch("/macro/briefing") }

    // MARK: - Screener

    func getScreenerWatchlist(allTime: Bool = false) async throws -> [WatchlistItem] {
        let path = allTime ? "/screening/watchlist?all_time=true&limit=50" : "/screening/watchlist?limit=50"
        return try await fetch(path)
    }
    func triggerScreening() async throws { try await post("/screening/run") }

    // MARK: - Research

    func getResearchWatchlist() async throws -> [ResearchMemo] { try await fetch("/research/watchlist") }
    func getMemo(ticker: String) async throws -> ResearchMemo  { try await fetch("/research/\(ticker.uppercased())/latest") }
    func triggerResearch(ticker: String) async throws          { try await post("/research/\(ticker.uppercased())") }

    // MARK: - Execution

    func getOrders() async throws -> [Order] { try await fetch("/execution/orders") }
}
