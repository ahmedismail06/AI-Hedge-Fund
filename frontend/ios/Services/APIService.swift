//
//  APIService.swift
//  TradingDashboard
//
//  Network service layer for API communication
//

import Foundation

// MARK: - API Error Types

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case networkError(Error)
    case decodingError(Error)
    case serverError(Int)
    case unauthorized
    case notFound
    case unknown
    
    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .invalidResponse:
            return "Invalid response from server"
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .decodingError(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        case .serverError(let code):
            return "Server error with status code: \(code)"
        case .unauthorized:
            return "Unauthorized - please log in"
        case .notFound:
            return "Resource not found"
        case .unknown:
            return "An unknown error occurred"
        }
    }
}

// MARK: - API Service

@MainActor
class APIService: ObservableObject {
    static let shared = APIService()
    
    private let session: URLSession
    private let baseURL: String
    
    @Published var isLoading = false
    @Published var error: APIError?
    
    private init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = AppConfig.API.timeout
        configuration.waitsForConnectivity = true
        self.session = URLSession(configuration: configuration)
        self.baseURL = AppConfig.API.baseURL
    }
    
    // MARK: - Generic Request Method
    
    func request<T: Decodable>(
        endpoint: String,
        method: HTTPMethod = .get,
        body: Encodable? = nil,
        headers: [String: String]? = nil
    ) async throws -> T {
        guard let url = URL(string: baseURL + endpoint) else {
            throw APIError.invalidURL
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = method.rawValue
        
        // Add headers
        request.addValue("application/json", forHTTPHeaderField: "Content-Type")
        headers?.forEach { key, value in
            request.addValue(value, forHTTPHeaderField: key)
        }
        
        // Add authentication token if available
        if let token = UserDefaults.standard.string(forKey: "authToken") {
            request.addValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        
        // Add body if provided
        if let body = body {
            do {
                request.httpBody = try JSONEncoder().encode(body)
            } catch {
                throw APIError.decodingError(error)
            }
        }
        
        // Perform request
        isLoading = true
        defer { isLoading = false }
        
        do {
            let (data, response) = try await session.data(for: request)
            
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            
            switch httpResponse.statusCode {
            case 200...299:
                do {
                    let decoded = try JSONDecoder().decode(T.self, from: data)
                    return decoded
                } catch {
                    throw APIError.decodingError(error)
                }
            case 401:
                throw APIError.unauthorized
            case 404:
                throw APIError.notFound
            default:
                throw APIError.serverError(httpResponse.statusCode)
            }
        } catch let error as APIError {
            self.error = error
            throw error
        } catch {
            let apiError = APIError.networkError(error)
            self.error = apiError
            throw apiError
        }
    }
    
    // MARK: - Portfolio Endpoints
    
    func fetchPortfolio() async throws -> PortfolioResponse {
        try await request(endpoint: AppConfig.API.portfolio)
    }
    
    func fetchPositions() async throws -> [PositionResponse] {
        try await request(endpoint: AppConfig.API.positions)
    }
    
    // MARK: - Order Endpoints
    
    func fetchOrders() async throws -> [OrderResponse] {
        try await request(endpoint: AppConfig.API.orders)
    }
    
    func placeOrder(_ order: OrderRequest) async throws -> OrderResponse {
        try await request(
            endpoint: AppConfig.API.orders,
            method: .post,
            body: order
        )
    }
    
    func cancelOrder(id: String) async throws -> CancelOrderResponse {
        try await request(
            endpoint: "\(AppConfig.API.orders)/\(id)",
            method: .delete
        )
    }
    
    // MARK: - Stock Endpoints
    
    func searchStocks(query: String) async throws -> [StockSearchResult] {
        try await request(endpoint: "\(AppConfig.API.stocks)/search?q=\(query)")
    }
    
    func fetchStockDetail(symbol: String) async throws -> StockDetailResponse {
        try await request(endpoint: "\(AppConfig.API.stocks)/\(symbol)")
    }
    
    func fetchStockQuote(symbol: String) async throws -> StockQuote {
        try await request(endpoint: "\(AppConfig.API.stocks)/\(symbol)/quote")
    }
    
    // MARK: - Research Endpoints
    
    func fetchNews(limit: Int = 20) async throws -> [NewsItem] {
        try await request(endpoint: "\(AppConfig.API.news)?limit=\(limit)")
    }
    
    func fetchResearch() async throws -> [ResearchItemResponse] {
        try await request(endpoint: AppConfig.API.research)
    }
}

// MARK: - HTTP Method

enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
    case put = "PUT"
    case patch = "PATCH"
    case delete = "DELETE"
}

// MARK: - Response Models

struct PortfolioResponse: Codable {
    let totalValue: Double
    let totalGainLoss: Double
    let totalGainLossPercent: Double
    let dayPnL: Double
    let buyingPower: Double
    let cash: Double
}

struct PositionResponse: Codable {
    let id: String
    let symbol: String
    let name: String
    let shares: Double
    let avgCost: Double
    let currentPrice: Double
    let marketValue: Double
    let gainLoss: Double
    let gainLossPercent: Double
}

struct OrderResponse: Codable {
    let id: String
    let symbol: String
    let type: String
    let side: String
    let quantity: Double
    let price: Double?
    let stopPrice: Double?
    let status: String
    let createdAt: String
    let updatedAt: String
}

struct OrderRequest: Codable {
    let symbol: String
    let type: String
    let side: String
    let quantity: Double
    let price: Double?
    let stopPrice: Double?
}

struct CancelOrderResponse: Codable {
    let success: Bool
    let message: String
}

struct StockSearchResult: Codable, Identifiable {
    let id: String
    let symbol: String
    let name: String
    let exchange: String
}

struct StockDetailResponse: Codable {
    let symbol: String
    let name: String
    let price: Double
    let change: Double
    let changePercent: Double
    let marketCap: String
    let peRatio: Double?
    let volume: Int64
    let avgVolume: Int64
    let high52Week: Double
    let low52Week: Double
}

struct StockQuote: Codable {
    let symbol: String
    let price: Double
    let change: Double
    let changePercent: Double
    let volume: Int64
    let timestamp: String
}

struct NewsItem: Codable, Identifiable {
    let id: String
    let title: String
    let summary: String
    let source: String
    let url: String
    let publishedAt: String
    let imageUrl: String?
}

struct ResearchItemResponse: Codable, Identifiable {
    let id: String
    let title: String
    let source: String
    let category: String
    let publishedAt: String
    let url: String
}

// MARK: - Mock Data Generator (for testing)

extension APIService {
    static func generateMockPortfolio() -> PortfolioResponse {
        PortfolioResponse(
            totalValue: 124567.89,
            totalGainLoss: 24567.89,
            totalGainLossPercent: 24.5,
            dayPnL: 1234.56,
            buyingPower: 50000.00,
            cash: 5432.11
        )
    }
    
    static func generateMockPositions() -> [PositionResponse] {
        [
            PositionResponse(
                id: "1",
                symbol: "AAPL",
                name: "Apple Inc.",
                shares: 100,
                avgCost: 150.50,
                currentPrice: 175.50,
                marketValue: 17550.00,
                gainLoss: 2500.00,
                gainLossPercent: 16.61
            ),
            PositionResponse(
                id: "2",
                symbol: "GOOGL",
                name: "Alphabet Inc.",
                shares: 50,
                avgCost: 120.00,
                currentPrice: 135.75,
                marketValue: 6787.50,
                gainLoss: 787.50,
                gainLossPercent: 13.13
            )
        ]
    }
    
    static func generateMockOrders() -> [OrderResponse] {
        [
            OrderResponse(
                id: "1",
                symbol: "AAPL",
                type: "Limit",
                side: "Buy",
                quantity: 100,
                price: 170.00,
                stopPrice: nil,
                status: "Pending",
                createdAt: ISO8601DateFormatter().string(from: Date()),
                updatedAt: ISO8601DateFormatter().string(from: Date())
            )
        ]
    }
}
