//
//  DashboardView.swift
//  TradingDashboard
//
//  Main dashboard overview with key metrics and charts
//

import SwiftUI
import Charts

struct DashboardView: View {
    @EnvironmentObject var themeManager: ThemeManager
    @State private var showSettings = false
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    // Portfolio Summary Card
                    PortfolioSummaryCard()
                    
                    // Performance Chart
                    PerformanceChartCard()
                    
                    // Quick Stats Grid
                    QuickStatsGrid()
                    
                    // Recent Activity
                    RecentActivityCard()
                }
                .padding()
            }
            .navigationTitle("Dashboard")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .background(Color(.systemGroupedBackground))
        }
    }
}

struct PortfolioSummaryCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Portfolio Value")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            
            HStack(alignment: .firstTextBaseline) {
                Text("$124,567.89")
                    .font(.system(size: 32, weight: .bold, design: .rounded))
                
                Spacer()
                
                HStack(spacing: 4) {
                    Image(systemName: "arrow.up.right")
                        .font(.caption)
                    Text("+2.34%")
                        .font(.subheadline)
                }
                .foregroundStyle(.green)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(.green.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            
            HStack {
                VStack(alignment: .leading) {
                    Text("Today's P&L")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("+$1,234.56")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.green)
                }
                
                Spacer()
                
                VStack(alignment: .trailing) {
                    Text("Total Return")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("+$24,567.89")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                }
            }
            .padding(.top, 4)
        }
        .padding()
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
    }
}

struct PerformanceChartCard: View {
    @State private var selectedPeriod: TimePeriod = .oneMonth
    
    enum TimePeriod: String, CaseIterable {
        case oneDay = "1D"
        case oneWeek = "1W"
        case oneMonth = "1M"
        case threeMonths = "3M"
        case oneYear = "1Y"
        case all = "All"
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Performance")
                    .font(.headline)
                
                Spacer()
                
                Picker("Period", selection: $selectedPeriod) {
                    ForEach(TimePeriod.allCases, id: \.self) { period in
                        Text(period.rawValue).tag(period)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 240)
            }
            
            // Sample chart - replace with real data
            Chart {
                ForEach(sampleChartData) { item in
                    LineMark(
                        x: .value("Date", item.date),
                        y: .value("Value", item.value)
                    )
                    .foregroundStyle(.blue.gradient)
                    .interpolationMethod(.catmullRom)
                    
                    AreaMark(
                        x: .value("Date", item.date),
                        y: .value("Value", item.value)
                    )
                    .foregroundStyle(.blue.opacity(0.1).gradient)
                    .interpolationMethod(.catmullRom)
                }
            }
            .frame(height: 200)
            .chartYAxis {
                AxisMarks(position: .leading)
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
    }
    
    private var sampleChartData: [ChartDataPoint] {
        let calendar = Calendar.current
        let endDate = Date()
        return (0..<30).reversed().map { day in
            let date = calendar.date(byAdding: .day, value: -day, to: endDate)!
            let value = 100000.0 + Double.random(in: -5000...15000)
            return ChartDataPoint(date: date, value: value)
        }
    }
}

struct ChartDataPoint: Identifiable {
    let id = UUID()
    let date: Date
    let value: Double
}

struct QuickStatsGrid: View {
    let stats = [
        StatItem(title: "Win Rate", value: "67.8%", icon: "chart.bar.fill", color: .green),
        StatItem(title: "Sharpe Ratio", value: "1.85", icon: "waveform.path.ecg", color: .blue),
        StatItem(title: "Max Drawdown", value: "-8.2%", icon: "arrow.down.right", color: .red),
        StatItem(title: "Positions", value: "12", icon: "square.grid.2x2.fill", color: .orange)
    ]
    
    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            ForEach(stats) { stat in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Image(systemName: stat.icon)
                            .foregroundStyle(stat.color)
                        Spacer()
                    }
                    
                    Text(stat.value)
                        .font(.title2)
                        .fontWeight(.bold)
                    
                    Text(stat.title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .background(Color(.systemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
            }
        }
    }
}

struct StatItem: Identifiable {
    let id = UUID()
    let title: String
    let value: String
    let icon: String
    let color: Color
}

struct RecentActivityCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Activity")
                .font(.headline)
            
            VStack(spacing: 12) {
                ActivityRow(
                    icon: "arrow.up.circle.fill",
                    iconColor: .green,
                    title: "Bought AAPL",
                    subtitle: "100 shares @ $175.50",
                    time: "2h ago"
                )
                
                ActivityRow(
                    icon: "arrow.down.circle.fill",
                    iconColor: .red,
                    title: "Sold TSLA",
                    subtitle: "50 shares @ $245.30",
                    time: "5h ago"
                )
                
                ActivityRow(
                    icon: "bell.fill",
                    iconColor: .orange,
                    title: "Price Alert",
                    subtitle: "NVDA reached $850",
                    time: "1d ago"
                )
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
    }
}

struct ActivityRow: View {
    let icon: String
    let iconColor: Color
    let title: String
    let subtitle: String
    let time: String
    
    var body: some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(iconColor)
                .font(.title3)
            
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline)
                    .fontWeight(.medium)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            Text(time)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

#Preview {
    DashboardView()
        .environmentObject(ThemeManager())
}
