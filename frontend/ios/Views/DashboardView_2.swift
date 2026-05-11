//
//  DashboardView.swift
//  TradingDashboard
//
//  Main dashboard with overview widgets and charts
//

import SwiftUI
import Charts

struct DashboardView: View {
    @State private var selectedTimeframe: TimeFrame = .day
    @EnvironmentObject var themeManager: ThemeManager
    
    enum TimeFrame: String, CaseIterable {
        case day = "1D"
        case week = "1W"
        case month = "1M"
        case threeMonths = "3M"
        case year = "1Y"
        case all = "ALL"
    }
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    // Account Summary Card
                    AccountSummaryCard()
                    
                    // Portfolio Performance Chart
                    PortfolioPerformanceChart(timeframe: selectedTimeframe)
                    
                    // Timeframe Selector
                    TimeframeSelector(selection: $selectedTimeframe)
                    
                    // Quick Stats Grid
                    QuickStatsGrid()
                    
                    // Top Movers
                    TopMoversSection()
                    
                    // Recent Activity
                    RecentActivitySection()
                }
                .padding()
            }
            .navigationTitle("Dashboard")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            // Refresh action
                        } label: {
                            Label("Refresh", systemImage: "arrow.clockwise")
                        }
                        
                        Button {
                            // Settings action
                        } label: {
                            Label("Settings", systemImage: "gear")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
    }
}

struct AccountSummaryCard: View {
    var body: some View {
        VStack(spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Total Portfolio Value")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text("$124,567.89")
                        .font(.system(size: 34, weight: .bold))
                }
                
                Spacer()
                
                VStack(alignment: .trailing, spacing: 4) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.up.right")
                        Text("24.5%")
                    }
                    .font(.title3)
                    .fontWeight(.semibold)
                    .foregroundStyle(.green)
                    
                    Text("+$24,567.89")
                        .font(.subheadline)
                        .foregroundStyle(.green)
                }
            }
            
            Divider()
            
            HStack(spacing: 20) {
                SummaryItem(title: "Day P&L", value: "+$1,234.56", isPositive: true)
                Divider()
                SummaryItem(title: "Buying Power", value: "$50,000.00", isPositive: nil)
                Divider()
                SummaryItem(title: "Cash", value: "$5,432.11", isPositive: nil)
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
}

struct SummaryItem: View {
    let title: String
    let value: String
    let isPositive: Bool?
    
    var body: some View {
        VStack(spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline)
                .fontWeight(.semibold)
                .foregroundStyle(isPositive == true ? .green : isPositive == false ? .red : .primary)
        }
        .frame(maxWidth: .infinity)
    }
}

struct PortfolioPerformanceChart: View {
    let timeframe: DashboardView.TimeFrame
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Performance")
                .font(.headline)
            
            Chart(sampleChartData) { item in
                LineMark(
                    x: .value("Date", item.date),
                    y: .value("Value", item.value)
                )
                .foregroundStyle(Color.blue.gradient)
                .interpolationMethod(.catmullRom)
                
                AreaMark(
                    x: .value("Date", item.date),
                    y: .value("Value", item.value)
                )
                .foregroundStyle(Color.blue.opacity(0.1).gradient)
                .interpolationMethod(.catmullRom)
            }
            .frame(height: 200)
            .chartYAxis {
                AxisMarks(position: .leading)
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
    
    private var sampleChartData: [ChartDataPoint] {
        let calendar = Calendar.current
        let now = Date()
        return (0..<30).map { i in
            let date = calendar.date(byAdding: .day, value: -29 + i, to: now)!
            let value = 100000.0 + Double.random(in: -5000...25000)
            return ChartDataPoint(date: date, value: value)
        }
    }
}

struct ChartDataPoint: Identifiable {
    let id = UUID()
    let date: Date
    let value: Double
}

struct TimeframeSelector: View {
    @Binding var selection: DashboardView.TimeFrame
    
    var body: some View {
        HStack(spacing: 8) {
            ForEach(DashboardView.TimeFrame.allCases, id: \.self) { timeframe in
                Button {
                    selection = timeframe
                } label: {
                    Text(timeframe.rawValue)
                        .font(.caption)
                        .fontWeight(.medium)
                        .foregroundStyle(selection == timeframe ? .white : .primary)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(selection == timeframe ? Color.blue : Color(.systemGray5))
                        .cornerRadius(8)
                }
            }
        }
    }
}

struct QuickStatsGrid: View {
    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            QuickStatCard(title: "Win Rate", value: "68.5%", icon: "chart.pie.fill", color: .green)
            QuickStatCard(title: "Positions", value: "12", icon: "list.bullet", color: .blue)
            QuickStatCard(title: "Avg. Return", value: "12.3%", icon: "arrow.up.right", color: .orange)
            QuickStatCard(title: "Sharpe Ratio", value: "1.85", icon: "gauge.high", color: .purple)
        }
    }
}

struct QuickStatCard: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: icon)
                    .foregroundStyle(color)
                Spacer()
            }
            
            Text(value)
                .font(.title2)
                .fontWeight(.bold)
            
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
}

struct TopMoversSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Top Movers")
                .font(.headline)
            
            VStack(spacing: 8) {
                ForEach(sampleTopMovers) { mover in
                    TopMoverRow(mover: mover)
                }
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
    
    private let sampleTopMovers = [
        TopMover(symbol: "NVDA", name: "NVIDIA", change: 8.5, price: 875.30),
        TopMover(symbol: "TSLA", name: "Tesla", change: -3.2, price: 245.60),
        TopMover(symbol: "AAPL", name: "Apple", change: 2.1, price: 175.50)
    ]
}

struct TopMover: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let change: Double
    let price: Double
}

struct TopMoverRow: View {
    let mover: TopMover
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(mover.symbol)
                    .font(.subheadline)
                    .fontWeight(.semibold)
                Text(mover.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 2) {
                Text("$\(mover.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                HStack(spacing: 4) {
                    Image(systemName: mover.change >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(abs(mover.change), specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(mover.change >= 0 ? .green : .red)
            }
        }
        .padding(.vertical, 4)
    }
}

struct RecentActivitySection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Recent Activity")
                    .font(.headline)
                
                Spacer()
                
                NavigationLink {
                    Text("All Activity")
                } label: {
                    Text("See All")
                        .font(.caption)
                        .foregroundStyle(.blue)
                }
            }
            
            VStack(spacing: 12) {
                ForEach(sampleActivities) { activity in
                    ActivityRow(activity: activity)
                }
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 5, x: 0, y: 2)
    }
    
    private let sampleActivities = [
        Activity(type: "Buy", symbol: "AAPL", shares: 10, price: 175.50, date: Date()),
        Activity(type: "Sell", symbol: "TSLA", shares: 5, price: 245.60, date: Date().addingTimeInterval(-3600)),
        Activity(type: "Buy", symbol: "GOOGL", shares: 15, price: 135.75, date: Date().addingTimeInterval(-7200))
    ]
}

struct Activity: Identifiable {
    let id = UUID()
    let type: String
    let symbol: String
    let shares: Double
    let price: Double
    let date: Date
}

struct ActivityRow: View {
    let activity: Activity
    
    var body: some View {
        HStack {
            Image(systemName: activity.type == "Buy" ? "arrow.down.circle.fill" : "arrow.up.circle.fill")
                .foregroundStyle(activity.type == "Buy" ? .green : .red)
                .font(.title3)
            
            VStack(alignment: .leading, spacing: 2) {
                Text("\(activity.type) \(activity.symbol)")
                    .font(.subheadline)
                    .fontWeight(.medium)
                Text("\(activity.shares, specifier: "%.0f") shares @ $\(activity.price, specifier: "%.2f")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            Text(activity.date, style: .relative)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

#Preview {
    DashboardView()
        .environmentObject(ThemeManager())
}
