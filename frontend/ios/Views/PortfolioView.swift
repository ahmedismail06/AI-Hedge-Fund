//
//  PortfolioView.swift
//  TradingDashboard
//
//  Portfolio holdings and positions management
//

import SwiftUI

struct PortfolioView: View {
    @State private var searchText = ""
    @State private var sortOrder: SortOrder = .value
    
    enum SortOrder {
        case value, performance, alphabetical
    }
    
    var body: some View {
        NavigationStack {
            List {
                Section {
                    PortfolioHeaderView()
                } header: {
                    Text("Total Holdings")
                }
                
                Section {
                    ForEach(filteredPositions) { position in
                        NavigationLink(destination: PositionDetailView(position: position)) {
                            PositionRow(position: position)
                        }
                    }
                } header: {
                    HStack {
                        Text("Positions")
                        Spacer()
                        Menu {
                            Button("By Value") { sortOrder = .value }
                            Button("By Performance") { sortOrder = .performance }
                            Button("Alphabetical") { sortOrder = .alphabetical }
                        } label: {
                            Label("Sort", systemImage: "arrow.up.arrow.down")
                                .font(.caption)
                        }
                    }
                }
            }
            .searchable(text: $searchText, prompt: "Search positions")
            .navigationTitle("Portfolio")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        // Add position action
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
        }
    }
    
    private var filteredPositions: [Position] {
        var positions = samplePositions
        
        // Filter by search
        if !searchText.isEmpty {
            positions = positions.filter { $0.symbol.localizedCaseInsensitiveContains(searchText) }
        }
        
        // Sort
        switch sortOrder {
        case .value:
            positions.sort { $0.currentValue > $1.currentValue }
        case .performance:
            positions.sort { $0.percentChange > $1.percentChange }
        case .alphabetical:
            positions.sort { $0.symbol < $1.symbol }
        }
        
        return positions
    }
    
    private let samplePositions = [
        Position(symbol: "AAPL", name: "Apple Inc.", shares: 100, avgCost: 150.50, currentPrice: 175.50),
        Position(symbol: "GOOGL", name: "Alphabet Inc.", shares: 50, avgCost: 120.00, currentPrice: 135.75),
        Position(symbol: "MSFT", name: "Microsoft Corp.", shares: 75, avgCost: 310.00, currentPrice: 398.50),
        Position(symbol: "NVDA", name: "NVIDIA Corp.", shares: 30, avgCost: 450.00, currentPrice: 875.30),
        Position(symbol: "TSLA", name: "Tesla Inc.", shares: 25, avgCost: 210.00, currentPrice: 245.60)
    ]
}

struct PortfolioHeaderView: View {
    var body: some View {
        VStack(spacing: 8) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Total Value")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("$124,567.89")
                        .font(.title)
                        .fontWeight(.bold)
                }
                
                Spacer()
                
                VStack(alignment: .trailing) {
                    Text("Total Gain/Loss")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.up")
                            .font(.caption)
                        Text("$24,567.89 (24.5%)")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                    }
                    .foregroundStyle(.green)
                }
            }
            
            Divider()
                .padding(.vertical, 4)
            
            HStack {
                StatColumn(title: "Day P&L", value: "+$1,234.56", color: .green)
                Divider()
                StatColumn(title: "Cost Basis", value: "$100,000.00", color: .primary)
                Divider()
                StatColumn(title: "Cash", value: "$5,432.11", color: .primary)
            }
            .frame(height: 40)
        }
    }
}

struct StatColumn: View {
    let title: String
    let value: String
    let color: Color
    
    var body: some View {
        VStack(spacing: 4) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption)
                .fontWeight(.medium)
                .foregroundStyle(color)
        }
        .frame(maxWidth: .infinity)
    }
}

struct PositionRow: View {
    let position: Position
    
    var body: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text(position.symbol)
                    .font(.headline)
                Text(position.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 4) {
                Text(position.currentValue, format: .currency(code: "USD"))
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                HStack(spacing: 4) {
                    Image(systemName: position.percentChange >= 0 ? "arrow.up" : "arrow.down")
                        .font(.caption2)
                    Text("\(position.percentChange, specifier: "%.2f")%")
                        .font(.caption)
                }
                .foregroundStyle(position.percentChange >= 0 ? .green : .red)
            }
        }
        .padding(.vertical, 4)
    }
}

struct Position: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let shares: Double
    let avgCost: Double
    let currentPrice: Double
    
    var currentValue: Double {
        shares * currentPrice
    }
    
    var costBasis: Double {
        shares * avgCost
    }
    
    var gainLoss: Double {
        currentValue - costBasis
    }
    
    var percentChange: Double {
        ((currentPrice - avgCost) / avgCost) * 100
    }
}

struct PositionDetailView: View {
    let position: Position
    
    var body: some View {
        List {
            Section("Position Details") {
                DetailRow(label: "Symbol", value: position.symbol)
                DetailRow(label: "Shares", value: "\(position.shares, specifier: "%.2f")")
                DetailRow(label: "Avg Cost", value: position.avgCost, format: .currency(code: "USD"))
                DetailRow(label: "Current Price", value: position.currentPrice, format: .currency(code: "USD"))
                DetailRow(label: "Market Value", value: position.currentValue, format: .currency(code: "USD"))
                DetailRow(label: "Total Gain/Loss", value: position.gainLoss, format: .currency(code: "USD"))
            }
            
            Section("Actions") {
                Button("Buy More") {
                    // Buy action
                }
                
                Button("Sell") {
                    // Sell action
                }
                .foregroundStyle(.red)
            }
        }
        .navigationTitle(position.symbol)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct DetailRow: View {
    let label: String
    let value: String
    
    init(label: String, value: String) {
        self.label = label
        self.value = value
    }
    
    init(label: String, value: Double, format: FloatingPointFormatStyle<Double>.Currency) {
        self.label = label
        self.value = value.formatted(format)
    }
    
    var body: some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
    }
}

#Preview {
    PortfolioView()
}
