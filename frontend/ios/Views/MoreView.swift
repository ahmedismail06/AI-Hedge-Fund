//
//  MoreView.swift
//  TradingDashboard
//
//  Additional features including Screener, Macro, Risk, and Orchestrator
//

import SwiftUI

struct MoreView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("Trading Tools") {
                    NavigationLink(destination: ScreenerView()) {
                        MenuRow(
                            icon: "line.3.horizontal.decrease.circle.fill",
                            title: "Screener",
                            subtitle: "Find stocks by criteria",
                            color: .blue
                        )
                    }
                    
                    NavigationLink(destination: MacroView()) {
                        MenuRow(
                            icon: "globe.americas.fill",
                            title: "Macro",
                            subtitle: "Economic indicators",
                            color: .green
                        )
                    }
                    
                    NavigationLink(destination: RiskView()) {
                        MenuRow(
                            icon: "shield.fill",
                            title: "Risk Analysis",
                            subtitle: "Portfolio risk metrics",
                            color: .orange
                        )
                    }
                    
                    NavigationLink(destination: OrchestratorView()) {
                        MenuRow(
                            icon: "wand.and.stars",
                            title: "Orchestrator",
                            subtitle: "Automated strategies",
                            color: .purple
                        )
                    }
                }
                
                Section("Account") {
                    NavigationLink(destination: SettingsView()) {
                        MenuRow(
                            icon: "gear",
                            title: "Settings",
                            subtitle: "App preferences",
                            color: .gray
                        )
                    }
                    
                    NavigationLink(destination: ProfileView()) {
                        MenuRow(
                            icon: "person.circle.fill",
                            title: "Profile",
                            subtitle: "Account details",
                            color: .indigo
                        )
                    }
                }
                
                Section("Support") {
                    Button {
                        // Help action
                    } label: {
                        MenuRow(
                            icon: "questionmark.circle.fill",
                            title: "Help & Support",
                            subtitle: "Get assistance",
                            color: .teal
                        )
                    }
                    
                    Button {
                        // About action
                    } label: {
                        MenuRow(
                            icon: "info.circle.fill",
                            title: "About",
                            subtitle: "Version 1.0.0",
                            color: .cyan
                        )
                    }
                }
            }
            .navigationTitle("More")
        }
    }
}

struct MenuRow: View {
    let icon: String
    let title: String
    let subtitle: String
    let color: Color
    
    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundStyle(color)
                .frame(width: 40)
            
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
        }
        .padding(.vertical, 8)
    }
}

// MARK: - Screener View

struct ScreenerView: View {
    @State private var minPrice: String = ""
    @State private var maxPrice: String = ""
    @State private var minMarketCap: String = ""
    @State private var sector: String = "All"
    @State private var showResults = false
    
    let sectors = ["All", "Technology", "Healthcare", "Finance", "Energy", "Consumer", "Industrial"]
    
    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Filters
                VStack(alignment: .leading, spacing: 16) {
                    Text("Filters")
                        .font(.headline)
                    
                    VStack(spacing: 16) {
                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Min Price")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                TextField("$0", text: $minPrice)
                                    .textFieldStyle(.roundedBorder)
                                    .keyboardType(.decimalPad)
                            }
                            
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Max Price")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                TextField("$1000", text: $maxPrice)
                                    .textFieldStyle(.roundedBorder)
                                    .keyboardType(.decimalPad)
                            }
                        }
                        
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Min Market Cap (B)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            TextField("0", text: $minMarketCap)
                                .textFieldStyle(.roundedBorder)
                                .keyboardType(.decimalPad)
                        }
                        
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Sector")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Picker("Sector", selection: $sector) {
                                ForEach(sectors, id: \.self) { sector in
                                    Text(sector).tag(sector)
                                }
                            }
                            .pickerStyle(.menu)
                        }
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Quick Presets
                VStack(alignment: .leading, spacing: 12) {
                    Text("Quick Presets")
                        .font(.headline)
                    
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 12) {
                            PresetChip(title: "Large Cap Growth", icon: "chart.line.uptrend.xyaxis")
                            PresetChip(title: "Dividend Stocks", icon: "dollarsign.circle.fill")
                            PresetChip(title: "High Volume", icon: "waveform")
                            PresetChip(title: "Tech Leaders", icon: "cpu")
                        }
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Run Screener Button
                Button {
                    showResults = true
                } label: {
                    Text("Run Screener")
                        .font(.headline)
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.blue)
                        .cornerRadius(12)
                }
                
                // Results
                if showResults {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Results (24)")
                            .font(.headline)
                        
                        ForEach(sampleScreenerResults) { result in
                            ScreenerResultRow(result: result)
                        }
                    }
                    .padding()
                    .background(Color(.systemBackground))
                    .cornerRadius(12)
                    .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                }
            }
            .padding()
        }
        .navigationTitle("Screener")
        .navigationBarTitleDisplayMode(.inline)
    }
    
    private let sampleScreenerResults = [
        ScreenerResult(symbol: "AAPL", name: "Apple Inc.", price: 175.50, change: 2.5, volume: "54.2M", pe: 28.5),
        ScreenerResult(symbol: "MSFT", name: "Microsoft Corp.", price: 398.50, change: 1.2, volume: "23.1M", pe: 35.2),
        ScreenerResult(symbol: "NVDA", name: "NVIDIA Corp.", price: 875.30, change: 8.5, volume: "41.5M", pe: 58.3)
    ]
}

struct PresetChip: View {
    let title: String
    let icon: String
    
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.caption)
            Text(title)
                .font(.subheadline)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color(.systemGray5))
        .cornerRadius(20)
    }
}

struct ScreenerResult: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let price: Double
    let change: Double
    let volume: String
    let pe: Double
}

struct ScreenerResultRow: View {
    let result: ScreenerResult
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(result.symbol)
                    .font(.headline)
                Text(result.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 4) {
                Text("$\(result.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                HStack(spacing: 4) {
                    Image(systemName: result.change >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(abs(result.change), specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(result.change >= 0 ? .green : .red)
            }
            
            VStack(alignment: .trailing, spacing: 4) {
                Text("Vol: \(result.volume)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("P/E: \(result.pe, specifier: "%.1f")")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

// MARK: - Macro View

struct MacroView: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Economic Indicators
                VStack(alignment: .leading, spacing: 12) {
                    Text("Economic Indicators")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        MacroIndicatorRow(name: "GDP Growth", value: "2.8%", trend: .up)
                        MacroIndicatorRow(name: "Unemployment", value: "3.7%", trend: .down)
                        MacroIndicatorRow(name: "Inflation (CPI)", value: "3.2%", trend: .down)
                        MacroIndicatorRow(name: "Fed Funds Rate", value: "5.25-5.50%", trend: .neutral)
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Market Sentiment
                VStack(alignment: .leading, spacing: 12) {
                    Text("Market Sentiment")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        SentimentRow(name: "VIX (Fear Index)", value: "14.25", sentiment: .low)
                        SentimentRow(name: "Put/Call Ratio", value: "0.85", sentiment: .neutral)
                        SentimentRow(name: "Market Breadth", value: "65% Bullish", sentiment: .high)
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Global Markets
                VStack(alignment: .leading, spacing: 12) {
                    Text("Global Markets")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        GlobalMarketRow(name: "Europe (STOXX 50)", value: "4,523.45", change: 0.8)
                        GlobalMarketRow(name: "Asia (Nikkei 225)", value: "38,450.12", change: 1.5)
                        GlobalMarketRow(name: "China (CSI 300)", value: "3,654.89", change: -0.3)
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
            }
            .padding()
        }
        .navigationTitle("Macro")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct MacroIndicatorRow: View {
    let name: String
    let value: String
    let trend: Trend
    
    enum Trend {
        case up, down, neutral
    }
    
    var body: some View {
        HStack {
            Text(name)
                .foregroundStyle(.secondary)
            
            Spacer()
            
            HStack(spacing: 8) {
                Text(value)
                    .fontWeight(.semibold)
                
                Image(systemName: trend == .up ? "arrow.up.circle.fill" : trend == .down ? "arrow.down.circle.fill" : "minus.circle.fill")
                    .foregroundStyle(trend == .up ? .green : trend == .down ? .red : .gray)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

struct SentimentRow: View {
    let name: String
    let value: String
    let sentiment: SentimentLevel
    
    enum SentimentLevel {
        case low, neutral, high
    }
    
    var body: some View {
        HStack {
            Text(name)
                .foregroundStyle(.secondary)
            
            Spacer()
            
            HStack(spacing: 8) {
                Text(value)
                    .fontWeight(.semibold)
                
                Circle()
                    .fill(sentiment == .high ? Color.green : sentiment == .low ? Color.red : Color.orange)
                    .frame(width: 12, height: 12)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

struct GlobalMarketRow: View {
    let name: String
    let value: String
    let change: Double
    
    var body: some View {
        HStack {
            Text(name)
                .foregroundStyle(.secondary)
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 2) {
                Text(value)
                    .fontWeight(.semibold)
                
                HStack(spacing: 4) {
                    Image(systemName: change >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(abs(change), specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(change >= 0 ? .green : .red)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

// MARK: - Risk View

struct RiskView: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Risk Score
                RiskScoreCard()
                
                // Risk Metrics
                VStack(alignment: .leading, spacing: 12) {
                    Text("Risk Metrics")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        RiskMetricRow(name: "Portfolio Beta", value: "1.15", benchmark: "vs S&P 500")
                        RiskMetricRow(name: "Sharpe Ratio", value: "1.85", benchmark: "Risk-adjusted return")
                        RiskMetricRow(name: "Max Drawdown", value: "-12.5%", benchmark: "Largest loss")
                        RiskMetricRow(name: "Volatility (30D)", value: "18.2%", benchmark: "Annualized")
                        RiskMetricRow(name: "Value at Risk", value: "$5,234", benchmark: "95% confidence")
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Concentration Risk
                VStack(alignment: .leading, spacing: 12) {
                    Text("Concentration Risk")
                        .font(.headline)
                    
                    VStack(spacing: 12) {
                        ConcentrationBar(sector: "Technology", percentage: 45)
                        ConcentrationBar(sector: "Healthcare", percentage: 20)
                        ConcentrationBar(sector: "Finance", percentage: 15)
                        ConcentrationBar(sector: "Consumer", percentage: 12)
                        ConcentrationBar(sector: "Other", percentage: 8)
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
            }
            .padding()
        }
        .navigationTitle("Risk Analysis")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct RiskScoreCard: View {
    var body: some View {
        VStack(spacing: 16) {
            Text("Overall Risk Score")
                .font(.headline)
                .foregroundStyle(.secondary)
            
            ZStack {
                Circle()
                    .stroke(Color(.systemGray5), lineWidth: 20)
                    .frame(width: 150, height: 150)
                
                Circle()
                    .trim(from: 0, to: 0.65)
                    .stroke(Color.orange.gradient, style: StrokeStyle(lineWidth: 20, lineCap: .round))
                    .frame(width: 150, height: 150)
                    .rotationEffect(.degrees(-90))
                
                VStack {
                    Text("65")
                        .font(.system(size: 48, weight: .bold))
                    Text("Moderate")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            
            Text("Your portfolio has moderate risk exposure")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
}

struct RiskMetricRow: View {
    let name: String
    let value: String
    let benchmark: String
    
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(name)
                    .font(.subheadline)
                Spacer()
                Text(value)
                    .font(.subheadline)
                    .fontWeight(.semibold)
            }
            
            Text(benchmark)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

struct ConcentrationBar: View {
    let sector: String
    let percentage: Double
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(sector)
                    .font(.subheadline)
                Spacer()
                Text("\(Int(percentage))%")
                    .font(.subheadline)
                    .fontWeight(.semibold)
            }
            
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(Color(.systemGray5))
                        .frame(height: 8)
                        .cornerRadius(4)
                    
                    Rectangle()
                        .fill(colorForPercentage(percentage))
                        .frame(width: geometry.size.width * (percentage / 100), height: 8)
                        .cornerRadius(4)
                }
            }
            .frame(height: 8)
        }
    }
    
    private func colorForPercentage(_ percentage: Double) -> Color {
        if percentage > 40 {
            return .red
        } else if percentage > 25 {
            return .orange
        } else {
            return .green
        }
    }
}

// MARK: - Orchestrator View

struct OrchestratorView: View {
    @State private var strategies: [Strategy] = [
        Strategy(name: "Momentum Trader", status: .active, returns: 12.5, trades: 45),
        Strategy(name: "Value Investor", status: .paused, returns: 8.3, trades: 12),
        Strategy(name: "Mean Reversion", status: .active, returns: 15.7, trades: 78)
    ]
    
    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Active Strategies
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Active Strategies")
                            .font(.headline)
                        
                        Spacer()
                        
                        Button {
                            // Add strategy
                        } label: {
                            Image(systemName: "plus.circle.fill")
                                .font(.title3)
                        }
                    }
                    
                    ForEach($strategies) { $strategy in
                        StrategyCard(strategy: $strategy)
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
                
                // Performance Summary
                VStack(alignment: .leading, spacing: 12) {
                    Text("Combined Performance")
                        .font(.headline)
                    
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        PerformanceMetric(label: "Total Return", value: "+36.5%")
                        PerformanceMetric(label: "Total Trades", value: "135")
                        PerformanceMetric(label: "Win Rate", value: "68%")
                        PerformanceMetric(label: "Avg Trade", value: "+$245")
                    }
                }
                .padding()
                .background(Color(.systemBackground))
                .cornerRadius(12)
                .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
            }
            .padding()
        }
        .navigationTitle("Orchestrator")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct Strategy: Identifiable {
    let id = UUID()
    let name: String
    var status: Status
    let returns: Double
    let trades: Int
    
    enum Status: String {
        case active = "Active"
        case paused = "Paused"
        case stopped = "Stopped"
    }
}

struct StrategyCard: View {
    @Binding var strategy: Strategy
    
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(strategy.name)
                        .font(.headline)
                    
                    HStack(spacing: 4) {
                        Circle()
                            .fill(strategy.status == .active ? Color.green : Color.orange)
                            .frame(width: 8, height: 8)
                        Text(strategy.status.rawValue)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                
                Spacer()
                
                Toggle("", isOn: Binding(
                    get: { strategy.status == .active },
                    set: { strategy.status = $0 ? .active : .paused }
                ))
                .labelsHidden()
            }
            
            Divider()
            
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Returns")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("+\(strategy.returns, specifier: "%.1f")%")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.green)
                }
                
                Spacer()
                
                VStack(alignment: .trailing, spacing: 4) {
                    Text("Trades")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("\(strategy.trades)")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                }
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }
}

struct PerformanceMetric: View {
    let label: String
    let value: String
    
    var body: some View {
        VStack(spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title3)
                .fontWeight(.bold)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

// MARK: - Settings View

struct SettingsView: View {
    @EnvironmentObject var themeManager: ThemeManager
    @State private var notificationsEnabled = true
    @State private var priceAlertsEnabled = true
    
    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: $themeManager.currentTheme) {
                    ForEach(ThemeManager.AppTheme.allCases, id: \.self) { theme in
                        Text(theme.rawValue).tag(theme)
                    }
                }
            }
            
            Section("Notifications") {
                Toggle("Enable Notifications", isOn: $notificationsEnabled)
                Toggle("Price Alerts", isOn: $priceAlertsEnabled)
            }
            
            Section("Data") {
                Button("Clear Cache") {
                    // Clear cache
                }
                
                Button("Export Data") {
                    // Export data
                }
            }
            
            Section {
                Button("Sign Out", role: .destructive) {
                    // Sign out
                }
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Profile View

struct ProfileView: View {
    var body: some View {
        Form {
            Section {
                HStack {
                    Spacer()
                    VStack(spacing: 12) {
                        Image(systemName: "person.circle.fill")
                            .font(.system(size: 80))
                            .foregroundStyle(.blue)
                        
                        Text("John Trader")
                            .font(.title2)
                            .fontWeight(.bold)
                        
                        Text("john.trader@example.com")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
            
            Section("Account Information") {
                LabeledContent("Account Number", value: "****5678")
                LabeledContent("Account Type", value: "Individual")
                LabeledContent("Member Since", value: "Jan 2024")
            }
            
            Section("Trading") {
                LabeledContent("Total Trades", value: "1,234")
                LabeledContent("Total Return", value: "+24.5%")
                LabeledContent("Win Rate", value: "68.5%")
            }
        }
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview {
    MoreView()
        .environmentObject(ThemeManager())
}
