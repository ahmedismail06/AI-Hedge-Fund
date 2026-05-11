//
//  AppConfig.swift
//  TradingDashboard
//
//  Configuration and constants for the app
//

import Foundation
import SwiftUI

enum AppConfig {
    // MARK: - App Information
    static let appName = "Trading Dashboard"
    static let appVersion = "1.0.0"
    static let buildNumber = "1"
    
    // MARK: - API Configuration
    enum API {
        static let baseURL = "https://api.yourtradingplatform.com"
        static let apiVersion = "v1"
        static let timeout: TimeInterval = 30
        
        // Endpoints
        static let portfolio = "/portfolio"
        static let positions = "/positions"
        static let orders = "/orders"
        static let stocks = "/stocks"
        static let news = "/news"
        static let research = "/research"
    }
    
    // MARK: - Feature Flags
    enum Features {
        static let enableRealTimeData = false
        static let enablePushNotifications = true
        static let enableBiometricAuth = true
        static let enableWidgets = false
        static let enableWatchApp = false
    }
    
    // MARK: - UI Configuration
    enum UI {
        static let animationDuration: Double = 0.3
        static let cardCornerRadius: CGFloat = 12
        static let cardShadowRadius: CGFloat = 5
        static let defaultPadding: CGFloat = 16
        
        // Colors
        static let profitColor = Color.green
        static let lossColor = Color.red
        static let neutralColor = Color.gray
        static let accentColor = Color.blue
    }
    
    // MARK: - Chart Configuration
    enum Charts {
        static let defaultTimeframe = "1D"
        static let availableTimeframes = ["1D", "1W", "1M", "3M", "1Y", "ALL"]
        static let chartHeight: CGFloat = 200
    }
    
    // MARK: - Trading Configuration
    enum Trading {
        static let minOrderValue: Double = 1.0
        static let maxOrderValue: Double = 1_000_000.0
        static let defaultCommission: Double = 0.0
        static let supportedOrderTypes = ["Market", "Limit", "Stop", "Stop Limit"]
    }
    
    // MARK: - Refresh Intervals (in seconds)
    enum RefreshIntervals {
        static let portfolio: TimeInterval = 60
        static let positions: TimeInterval = 30
        static let orders: TimeInterval = 10
        static let marketData: TimeInterval = 5
    }
    
    // MARK: - Limits
    enum Limits {
        static let maxWatchlistSymbols = 50
        static let maxOpenOrders = 100
        static let maxPositions = 200
        static let searchResultsLimit = 20
    }
}

// MARK: - Number Formatters

extension AppConfig {
    enum Formatters {
        static let currency: NumberFormatter = {
            let formatter = NumberFormatter()
            formatter.numberStyle = .currency
            formatter.currencyCode = "USD"
            formatter.maximumFractionDigits = 2
            return formatter
        }()
        
        static let percent: NumberFormatter = {
            let formatter = NumberFormatter()
            formatter.numberStyle = .percent
            formatter.minimumFractionDigits = 2
            formatter.maximumFractionDigits = 2
            formatter.multiplier = 1
            return formatter
        }()
        
        static let decimal: NumberFormatter = {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            formatter.maximumFractionDigits = 2
            return formatter
        }()
        
        static let compactNumber: NumberFormatter = {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            formatter.notation = .compactName
            return formatter
        }()
    }
}

// MARK: - Date Formatters

extension AppConfig {
    enum DateFormatters {
        static let short: DateFormatter = {
            let formatter = DateFormatter()
            formatter.dateStyle = .short
            formatter.timeStyle = .none
            return formatter
        }()
        
        static let medium: DateFormatter = {
            let formatter = DateFormatter()
            formatter.dateStyle = .medium
            formatter.timeStyle = .short
            return formatter
        }()
        
        static let long: DateFormatter = {
            let formatter = DateFormatter()
            formatter.dateStyle = .long
            formatter.timeStyle = .medium
            return formatter
        }()
        
        static let time: DateFormatter = {
            let formatter = DateFormatter()
            formatter.dateStyle = .none
            formatter.timeStyle = .short
            return formatter
        }()
    }
}

// MARK: - Helper Extensions

extension Double {
    func toCurrency() -> String {
        AppConfig.Formatters.currency.string(from: NSNumber(value: self)) ?? "$0.00"
    }
    
    func toPercent() -> String {
        AppConfig.Formatters.percent.string(from: NSNumber(value: self)) ?? "0.00%"
    }
    
    func toCompact() -> String {
        AppConfig.Formatters.compactNumber.string(from: NSNumber(value: self)) ?? "0"
    }
}

extension Date {
    func toShortString() -> String {
        AppConfig.DateFormatters.short.string(from: self)
    }
    
    func toMediumString() -> String {
        AppConfig.DateFormatters.medium.string(from: self)
    }
    
    func toLongString() -> String {
        AppConfig.DateFormatters.long.string(from: self)
    }
    
    func toTimeString() -> String {
        AppConfig.DateFormatters.time.string(from: self)
    }
}
