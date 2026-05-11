//
//  ExecutionView.swift
//  TradingDashboard
//
//  Trade execution and order management
//

import SwiftUI

struct ExecutionView: View {
    @State private var selectedSegment: ExecutionSegment = .orders
    @State private var showingNewOrder = false
    
    enum ExecutionSegment: String, CaseIterable {
        case orders = "Orders"
        case history = "History"
        case fills = "Fills"
    }
    
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Segmented Control
                Picker("Execution View", selection: $selectedSegment) {
                    ForEach(ExecutionSegment.allCases, id: \.self) { segment in
                        Text(segment.rawValue).tag(segment)
                    }
                }
                .pickerStyle(.segmented)
                .padding()
                
                // Content
                switch selectedSegment {
                case .orders:
                    OrdersListView()
                case .history:
                    OrderHistoryView()
                case .fills:
                    FillsListView()
                }
            }
            .navigationTitle("Execution")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingNewOrder = true
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title3)
                    }
                }
            }
            .sheet(isPresented: $showingNewOrder) {
                NewOrderSheet()
            }
        }
    }
}

struct OrdersListView: View {
    var body: some View {
        List {
            if sampleOrders.isEmpty {
                ContentUnavailableView(
                    "No Active Orders",
                    systemImage: "tray",
                    description: Text("Place a new order to get started")
                )
            } else {
                ForEach(sampleOrders) { order in
                    OrderRow(order: order)
                }
            }
        }
        .listStyle(.plain)
    }
    
    private let sampleOrders = [
        Order(symbol: "AAPL", type: "Limit Buy", quantity: 100, price: 170.00, status: "Pending"),
        Order(symbol: "TSLA", type: "Stop Loss", quantity: 50, price: 240.00, status: "Active"),
        Order(symbol: "NVDA", type: "Limit Sell", quantity: 25, price: 900.00, status: "Pending")
    ]
}

struct Order: Identifiable {
    let id = UUID()
    let symbol: String
    let type: String
    let quantity: Double
    let price: Double
    let status: String
}

struct OrderRow: View {
    let order: Order
    
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(order.symbol)
                    .font(.headline)
                
                Text(order.type)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 4) {
                Text("\(order.quantity, specifier: "%.0f") @ $\(order.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .fontWeight(.medium)
                
                Text(order.status)
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(statusColor(order.status).opacity(0.2))
                    .foregroundStyle(statusColor(order.status))
                    .cornerRadius(4)
            }
        }
        .padding(.vertical, 4)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) {
                // Cancel order
            } label: {
                Label("Cancel", systemImage: "xmark")
            }
            
            Button {
                // Edit order
            } label: {
                Label("Edit", systemImage: "pencil")
            }
            .tint(.blue)
        }
    }
    
    private func statusColor(_ status: String) -> Color {
        switch status {
        case "Active": return .green
        case "Pending": return .orange
        case "Filled": return .blue
        default: return .gray
        }
    }
}

struct OrderHistoryView: View {
    var body: some View {
        List {
            ForEach(sampleHistory) { trade in
                TradeHistoryRow(trade: trade)
            }
        }
        .listStyle(.plain)
    }
    
    private let sampleHistory = [
        TradeHistory(symbol: "AAPL", type: "Buy", quantity: 50, price: 170.25, date: Date(), status: "Filled"),
        TradeHistory(symbol: "GOOGL", type: "Sell", quantity: 25, price: 135.50, date: Date().addingTimeInterval(-86400), status: "Filled"),
        TradeHistory(symbol: "MSFT", type: "Buy", quantity: 30, price: 395.75, date: Date().addingTimeInterval(-172800), status: "Filled"),
        TradeHistory(symbol: "TSLA", type: "Buy", quantity: 15, price: 242.00, date: Date().addingTimeInterval(-259200), status: "Cancelled")
    ]
}

struct TradeHistory: Identifiable {
    let id = UUID()
    let symbol: String
    let type: String
    let quantity: Double
    let price: Double
    let date: Date
    let status: String
}

struct TradeHistoryRow: View {
    let trade: TradeHistory
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(trade.symbol)
                    .font(.headline)
                
                Spacer()
                
                Text(trade.date, style: .date)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            
            HStack {
                Text("\(trade.type) \(trade.quantity, specifier: "%.0f") @ $\(trade.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                
                Spacer()
                
                Text(trade.status)
                    .font(.caption)
                    .fontWeight(.medium)
                    .foregroundStyle(trade.status == "Filled" ? .green : .red)
            }
        }
        .padding(.vertical, 4)
    }
}

struct FillsListView: View {
    var body: some View {
        List {
            ForEach(sampleFills) { fill in
                FillRow(fill: fill)
            }
        }
        .listStyle(.plain)
    }
    
    private let sampleFills = [
        Fill(symbol: "AAPL", quantity: 100, price: 170.50, time: Date(), side: "Buy"),
        Fill(symbol: "NVDA", quantity: 25, price: 875.25, time: Date().addingTimeInterval(-3600), side: "Buy"),
        Fill(symbol: "TSLA", quantity: 50, price: 246.00, time: Date().addingTimeInterval(-7200), side: "Sell")
    ]
}

struct Fill: Identifiable {
    let id = UUID()
    let symbol: String
    let quantity: Double
    let price: Double
    let time: Date
    let side: String
}

struct FillRow: View {
    let fill: Fill
    
    var body: some View {
        HStack {
            Image(systemName: fill.side == "Buy" ? "arrow.down.circle.fill" : "arrow.up.circle.fill")
                .foregroundStyle(fill.side == "Buy" ? .green : .red)
                .font(.title3)
            
            VStack(alignment: .leading, spacing: 4) {
                Text(fill.symbol)
                    .font(.headline)
                
                Text("\(fill.quantity, specifier: "%.0f") shares @ $\(fill.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            
            Spacer()
            
            VStack(alignment: .trailing, spacing: 4) {
                Text(fill.time, style: .time)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                
                Text("$\(fill.quantity * fill.price, specifier: "%.2f")")
                    .font(.subheadline)
                    .fontWeight(.medium)
            }
        }
        .padding(.vertical, 4)
    }
}

struct NewOrderSheet: View {
    @Environment(\.dismiss) var dismiss
    @State private var symbol = ""
    @State private var orderType: OrderType = .market
    @State private var side: OrderSide = .buy
    @State private var quantity = ""
    @State private var limitPrice = ""
    @State private var stopPrice = ""
    
    enum OrderType: String, CaseIterable {
        case market = "Market"
        case limit = "Limit"
        case stop = "Stop"
        case stopLimit = "Stop Limit"
    }
    
    enum OrderSide: String, CaseIterable {
        case buy = "Buy"
        case sell = "Sell"
    }
    
    var body: some View {
        NavigationStack {
            Form {
                Section("Order Details") {
                    TextField("Symbol", text: $symbol)
                        .textInputAutocapitalization(.characters)
                    
                    Picker("Side", selection: $side) {
                        ForEach(OrderSide.allCases, id: \.self) { side in
                            Text(side.rawValue).tag(side)
                        }
                    }
                    .pickerStyle(.segmented)
                    
                    Picker("Order Type", selection: $orderType) {
                        ForEach(OrderType.allCases, id: \.self) { type in
                            Text(type.rawValue).tag(type)
                        }
                    }
                    
                    TextField("Quantity", text: $quantity)
                        .keyboardType(.numberPad)
                }
                
                if orderType == .limit || orderType == .stopLimit {
                    Section("Limit Price") {
                        TextField("Price", text: $limitPrice)
                            .keyboardType(.decimalPad)
                    }
                }
                
                if orderType == .stop || orderType == .stopLimit {
                    Section("Stop Price") {
                        TextField("Stop Price", text: $stopPrice)
                            .keyboardType(.decimalPad)
                    }
                }
                
                Section {
                    Button(action: submitOrder) {
                        HStack {
                            Spacer()
                            Text("Place \(side.rawValue) Order")
                                .fontWeight(.semibold)
                            Spacer()
                        }
                    }
                    .disabled(symbol.isEmpty || quantity.isEmpty)
                }
            }
            .navigationTitle("New Order")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
        }
    }
    
    private func submitOrder() {
        // Submit order logic
        dismiss()
    }
}

#Preview {
    ExecutionView()
}
