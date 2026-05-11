//
//  ResearchView.swift
//  TradingDashboard
//
//  Research tools and stock analysis
//

import SwiftUI

struct ResearchView: View {
    @State private var searchText = ""
    @State private var selectedCategory: ResearchCategory = .all
    
    enum ResearchCategory: String, CaseIterable {
        case all = "All"
        case stocks = "Stocks"
        case earnings = "Earnings"
        case news = "News"
        case analysts = "Analysts"
    }
    
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Category Picker
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(ResearchCategory.allCases, id: \.self) { category in
                            CategoryChip(
                                title: category.rawValue,
                                isSelected: selectedCategory == category
                            ) {
                                selectedCategory = category
                            }
                        }
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                }
                
                Divider()
                
                // Content
                List {
                    Section("Market Overview") {
                        MarketIndicesView()
                    }
                    
                    Section("Watchlist") {
                        ForEach(sampleWatchlist) { stock in
                            NavigationLink(destination: StockDetailView(stock: stock)) {
                                WatchlistRow(stock: stock)
                            }
                        }
                    }
                    
                    Section("Recent Research") {
                        ForEach(sampleResearch) { item in
                            ResearchItemRow(item: item)
                        }
                    }
                    
                    Section("Earnings Calendar") {
                        ForEach(sampleEarnings) { earning in
                            EarningsRow(earning: earning)
                        }
                    }
                }
                .listStyle(.insetGrouped)
            }
            .navigationTitle("Research")
            .searchable(text: $searchText, prompt: "Search stocks, news...")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        // Add to watchlist
                    } label: {
                        Image(systemName: "plus.circle")
                    }
                }
            }
        }
    }
    
    private let sampleWatchlist = [
        Stock(symbol: "AAPL", name: "Apple Inc.", price: 175.50, change: 2.5, marketCap: "2.75T"),
        Stock(symbol: "GOOGL", name: "Alphabet Inc.", price: 135.75, change: -0.8, marketCap: "1.68T"),
        Stock(symbol: "MSFT", name: "Microsoft Corp.", price: 398.50, change: 1.2, marketCap: "2.96T"),
        Stock(symbol: "NVDA", name: "NVIDIA Corp.", price: 875.30, change: 8.5, marketCap: "2.16T")
    ]
    
    private let sampleResearch = [
        ResearchItem(title: "NVIDIA Announces New AI Chip", source: "TechNews", date: Date(), category: "Product"),
        ResearchItem(title: "Apple's Vision Pro Sales Beat Expectations", source: "MarketWatch", date: Date().addingTimeInterval(-3600), category: "Earnings"),
        ResearchItem(title: "Fed Signals Rate Cut in Q3", source: "Bloomberg", date: Date().addingTimeInterval(-7200), category: "Macro")
    ]
    
    private let sampleEarnings = [
        Earning(symbol: "AAPL", date: Date().addingTimeInterval(86400), estimate: "$1.54"),
        Earning(symbol: "GOOGL", date: Date().addingTimeInterval(172800), estimate: "$1.68"),
        Earning(symbol: "MSFT", date: Date().addingTimeInterval(259200), estimate: "$2.85")
    ]
}

struct CategoryChip: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline)
                .fontWeight(.medium)
                .foregroundStyle(isSelected ? .white : .primary)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(isSelected ? Color.blue : Color(.systemGray5))
                .cornerRadius(20)
        }
    }
}

struct MarketIndicesView: View {
    var body: some View {
        VStack(spacing: 12) {
            IndexRow(name: "S&P 500", symbol: "SPX", value: "5,234.18", change: 0.45)
            IndexRow(name: "Dow Jones", symbol: "DJI", value: "39,512.84", change: -0.12)
            IndexRow(name: "NASDAQ", symbol: "IXIC", value: "16,340.87", change: 1.23)
        }
    }
}

struct IndexRow: View {
    let name: String
    let symbol: String
    let value: String
    let change: Double
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.subheadline)
                    .fontWeight(.medium)
                Text(symbol)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 2) {
                Text(value)
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                HStack(spacing: 4) {
                    Image(systemName: change >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(abs(change), specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(change >= 0 ? .green : .red)
            }
        }
    }
}

struct Stock: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let price: Double
    let change: Double
    let marketCap: String
}

struct WatchlistRow: View {
    let stock: Stock
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(stock.symbol)
                    .font(.headline)
                Text(stock.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 4) {
                Text("$\(stock.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                HStack(spacing: 4) {
                    Image(systemName: stock.change >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(abs(stock.change), specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(stock.change >= 0 ? .green : .red)
            }
        }
        .padding(.vertical, 4)
    }
}

struct ResearchItem: Identifiable {
    let id = UUID()
    let title: String
    let source: String
    let date: Date
    let category: String
}

struct ResearchItemRow: View {
    let item: ResearchItem
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(item.title)
                .font(.subheadline)
                .fontWeight(.medium)
            
            HStack {
                Text(item.source)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                
                Text("•")
                    .foregroundStyle(.secondary)
                
                Text(item.date, style: .relative)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                
                Spacer()
                
                Text(item.category)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.blue.opacity(0.2))
                    .foregroundStyle(.blue)
                    .cornerRadius(4)
            }
        }
        .padding(.vertical, 4)
    }
}

struct Earning: Identifiable {
    let id = UUID()
    let symbol: String
    let date: Date
    let estimate: String
}

struct EarningsRow: View {
    let earning: Earning
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(earning.symbol)
                    .font(.headline)
                Text("EPS Est: \(earning.estimate)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            Text(earning.date, style: .date)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

struct StockDetailView: View {
    let stock: Stock
    @State private var selectedTab: StockTab = .overview
    
    enum StockTab: String, CaseIterable {
        case overview = "Overview"
        case chart = "Chart"
        case news = "News"
        case stats = "Stats"
    }
    
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Header
                VStack(alignment: .leading, spacing: 8) {
                    Text(stock.symbol)
                        .font(.largeTitle)
                        .fontWeight(.bold)
                    
                    Text(stock.name)
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("$\(stock.price, specifier: "%.2f")")
                            .font(.system(size: 36, weight: .bold))
                        
                        HStack(spacing: 4) {
                            Image(systemName: stock.change >= 0 ? "arrow.up" : "arrow.down")
                            Text("\(abs(stock.change), specifier: "%.2f")%")
                        }
                        .font(.title3)
                        .foregroundStyle(stock.change >= 0 ? .green : .red)
                    }
                }
                .padding()
                
                // Tabs
                Picker("Stock Info", selection: $selectedTab) {
                    ForEach(StockTab.allCases, id: \.self) { tab in
                        Text(tab.rawValue).tag(tab)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                
                // Tab Content
                Group {
                    switch selectedTab {
                    case .overview:
                        StockOverviewTab(stock: stock)
                    case .chart:
                        StockChartTab()
                    case .news:
                        StockNewsTab()
                    case .stats:
                        StockStatsTab()
                    }
                }
                .padding()
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    // Add to watchlist
                } label: {
                    Image(systemName: "star")
                }
            }
        }
    }
}

struct StockOverviewTab: View {
    let stock: Stock
    
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Key Stats")
                .font(.headline)
            
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 16) {
                KeyStatItem(label: "Market Cap", value: stock.marketCap)
                KeyStatItem(label: "P/E Ratio", value: "28.5")
                KeyStatItem(label: "52W High", value: "$199.62")
                KeyStatItem(label: "52W Low", value: "$124.17")
                KeyStatItem(label: "Volume", value: "54.2M")
                KeyStatItem(label: "Avg Volume", value: "58.1M")
            }
        }
    }
}

struct KeyStatItem: View {
    let label: String
    let value: String
    
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline)
                .fontWeight(.semibold)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

struct StockChartTab: View {
    var body: some View {
        VStack {
            Text("Chart View")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("Interactive price chart coming soon")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(height: 300)
    }
}

struct StockNewsTab: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Latest News")
                .font(.headline)
            
            ForEach(0..<5) { _ in
                VStack(alignment: .leading, spacing: 6) {
                    Text("Company announces quarterly earnings")
                        .font(.subheadline)
                        .fontWeight(.medium)
                    Text("2 hours ago • MarketWatch")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .background(Color(.systemGray6))
                .cornerRadius(8)
            }
        }
    }
}

struct StockStatsTab: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Financial Metrics")
                .font(.headline)
            
            VStack(spacing: 12) {
                StatRow(label: "Revenue (TTM)", value: "$394.3B")
                StatRow(label: "Net Income", value: "$97.0B")
                StatRow(label: "Operating Margin", value: "30.1%")
                StatRow(label: "ROE", value: "147.5%")
                StatRow(label: "Debt to Equity", value: "1.98")
                StatRow(label: "Current Ratio", value: "0.98")
            }
        }
    }
}

struct StatRow: View {
    let label: String
    let value: String
    
    var body: some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.semibold)
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(8)
    }
}

#Preview {
    ResearchView()
}
