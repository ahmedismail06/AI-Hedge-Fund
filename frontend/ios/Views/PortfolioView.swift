import SwiftUI

// MARK: - View

struct PortfolioView: View {
    @StateObject private var vm = PortfolioVM()
    @State private var tab: PortfolioTab = .open
    @State private var selectedPosition: Position?

    enum PortfolioTab: String, CaseIterable { case open = "Open", pending = "Pending", history = "History" }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if let exp = vm.exposure { ExposureBanner(exp: exp) }

                Picker("Tab", selection: $tab) {
                    ForEach(PortfolioTab.allCases, id: \.self) { t in
                        Text(tabLabel(t)).tag(t)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal).padding(.vertical, 8)

                Group {
                    switch tab {
                    case .open:
                        PositionList(positions: vm.open, onTap: { selectedPosition = $0 })
                    case .pending:
                        PendingList(positions: vm.pending,
                                    onApprove: { id in Task { await vm.approve(id) } },
                                    onReject:  { id in Task { await vm.reject(id) } })
                    case .history:
                        PositionList(positions: vm.history, onTap: { selectedPosition = $0 })
                    }
                }
                .frame(maxHeight: .infinity)
            }
            .navigationTitle("Portfolio")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { Task { await vm.load() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .sheet(item: $selectedPosition) { pos in PositionDetailSheet(position: pos) }
            .task { await vm.load() }
            .refreshable { await vm.load() }
            .overlay {
                if let err = vm.error {
                    VStack {
                        ErrorCard(message: err).padding()
                        Spacer()
                    }
                }
            }
        }
    }

    private func tabLabel(_ t: PortfolioTab) -> String {
        switch t {
        case .open:    return "Open (\(vm.open.count))"
        case .pending: return "Pending (\(vm.pending.count))"
        case .history: return "History"
        }
    }
}

// MARK: - Exposure Banner

private struct ExposureBanner: View {
    let exp: ExposureReport

    var body: some View {
        HStack(spacing: 0) {
            RegimeBadge(regime: exp.regime).padding(.leading)
            Spacer()
            BannerStat(label: "Gross", value: exp.grossExposurePct.pctString,
                       cap: exp.caps.maxGross, highlight: exp.grossExposurePct >= exp.caps.maxGross * 0.9)
            BannerStat(label: "Net",   value: exp.netExposurePct.pctString,   cap: nil, highlight: false)
            BannerStat(label: "Cap",   value: exp.caps.maxGross.pctString,    cap: nil, highlight: false)
        }
        .padding(.vertical, 10)
        .background(Color(.systemGroupedBackground))
    }
}

private struct BannerStat: View {
    let label: String; let value: String; let cap: Double?; let highlight: Bool
    var body: some View {
        VStack(spacing: 1) {
            Text(value).font(.subheadline.bold()).foregroundStyle(highlight ? .red : .primary)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }.padding(.horizontal, 12)
    }
}

// MARK: - Position List

private struct PositionList: View {
    let positions: [Position]
    let onTap: (Position) -> Void

    var body: some View {
        if positions.isEmpty {
            EmptyStateView(icon: "briefcase", title: "No Positions", subtitle: "Nothing to show here")
        } else {
            List(positions) { pos in
                Button { onTap(pos) } label: { PositionRow(position: pos) }
                    .buttonStyle(.plain)
            }
            .listStyle(.plain)
        }
    }
}

private struct PositionRow: View {
    let position: Position

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(position.ticker).font(.headline)
                    DirectionBadge(direction: position.direction)
                    Text(position.sizeLabel.capitalized)
                        .font(.caption2).foregroundStyle(.secondary)
                }
                ConvictionBar(score: position.convictionScore).frame(width: 120)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 4) {
                Text(String(format: "$%.0f", position.dollarSize))
                    .font(.subheadline.bold())
                if let pnl = position.pnl {
                    Text(pnl.dollarString)
                        .font(.caption.bold())
                        .foregroundStyle(pnl.pnlColor)
                }
                if let pct = position.pnlPct {
                    Text(String(format: "%.2f%%", pct * 100))
                        .font(.caption2)
                        .foregroundStyle(pct.pnlColor)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Pending List

private struct PendingList: View {
    let positions: [Position]
    let onApprove: (String) -> Void
    let onReject: (String) -> Void

    var body: some View {
        if positions.isEmpty {
            EmptyStateView(icon: "checkmark.circle", title: "No Pending Approvals",
                           subtitle: "The PM agent will queue new positions here")
        } else {
            List(positions) { pos in
                PendingRow(position: pos, onApprove: { onApprove(pos.id) }, onReject: { onReject(pos.id) })
            }
            .listStyle(.plain)
        }
    }
}

private struct PendingRow: View {
    let position: Position
    let onApprove: () -> Void
    let onReject: () -> Void
    @State private var showDetail = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(position.ticker).font(.headline)
                DirectionBadge(direction: position.direction)
                Text(position.sizeLabel.capitalized).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(String(format: "$%.0f", position.dollarSize)).font(.subheadline.bold())
            }

            HStack(spacing: 6) {
                Label(String(format: "%.0f shares", position.shareCount), systemImage: "number")
                    .font(.caption).foregroundStyle(.secondary)
                if let ep = position.entryPrice {
                    Text("@ $\(String(format: "%.2f", ep))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let regime = position.regimeAtSizing {
                    RegimeBadge(regime: regime)
                }
            }

            ConvictionBar(score: position.convictionScore)

            if showDetail, let rationale = position.sizingRationale {
                Text(rationale).font(.caption).foregroundStyle(.secondary)
                    .padding(.vertical, 4)
            }

            HStack(spacing: 12) {
                Button("Approve") { onApprove() }
                    .buttonStyle(.borderedProminent).tint(.green).frame(maxWidth: .infinity)
                Button("Reject") { onReject() }
                    .buttonStyle(.bordered).tint(.red).frame(maxWidth: .infinity)
                Button(showDetail ? "Less" : "More") { showDetail.toggle() }
                    .buttonStyle(.borderless).font(.caption).tint(.secondary)
            }
        }
        .padding(.vertical, 6)
    }
}

// MARK: - Position Detail Sheet

private struct PositionDetailSheet: View {
    let position: Position
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Overview") {
                    DetailRow("Ticker",    position.ticker)
                    DetailRow("Direction", position.direction)
                    DetailRow("Status",    position.status)
                    DetailRow("Size",      position.sizeLabel.capitalized)
                    DetailRow("Sector",    position.sector ?? "—")
                }
                Section("Sizing") {
                    DetailRow("Dollar Size",   String(format: "$%.0f", position.dollarSize))
                    DetailRow("Share Count",   String(format: "%.2f", position.shareCount))
                    DetailRow("% Portfolio",   String(format: "%.1f%%", position.pctOfPortfolio * 100))
                    DetailRow("Kelly Fraction", String(format: "%.4f", position.kellyFraction))
                    DetailRow("Conviction",    String(format: "%.1f/10", position.convictionScore))
                }
                Section("Prices") {
                    DetailRow("Entry",       position.entryPrice.map    { String(format: "$%.2f", $0) } ?? "—")
                    DetailRow("Current",     position.currentPrice.map  { String(format: "$%.2f", $0) } ?? "—")
                    DetailRow("Stop Loss",   position.stopLossPrice.map { String(format: "$%.2f", $0) } ?? "—")
                    DetailRow("Target",      position.targetPrice.map   { String(format: "$%.2f", $0) } ?? "—")
                    if let rr = position.riskRewardRatio { DetailRow("R/R", String(format: "%.2f", rr)) }
                }
                if let pnl = position.pnl {
                    Section("P&L") {
                        DetailRow("Unrealised P&L", pnl.dollarString, color: pnl.pnlColor)
                        if let pct = position.pnlPct {
                            DetailRow("P&L %", String(format: "%.2f%%", pct * 100), color: pct.pnlColor)
                        }
                    }
                }
                if let rationale = position.sizingRationale {
                    Section("Rationale") {
                        Text(rationale).font(.subheadline)
                    }
                }
                Section("Stop Tiers") {
                    if let t1 = position.stopTier1 { DetailRow("Tier 1 (−8%)", String(format: "$%.2f", t1)) }
                    if let t2 = position.stopTier2 { DetailRow("Tier 2 (−15%)", String(format: "$%.2f", t2)) }
                    if let t3 = position.stopTier3 { DetailRow("Tier 3 (−20%)", String(format: "$%.2f", t3)) }
                }
            }
            .navigationTitle(position.ticker)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

private struct DetailRow: View {
    let label: String; let value: String; var color: Color = .primary
    init(_ label: String, _ value: String, color: Color = .primary) {
        self.label = label; self.value = value; self.color = color
    }
    var body: some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).bold().foregroundStyle(color)
        }
    }
}

// MARK: - ViewModel

@MainActor
final class PortfolioVM: ObservableObject {
    @Published var open:     [Position] = []
    @Published var pending:  [Position] = []
    @Published var history:  [Position] = []
    @Published var exposure: ExposureReport?
    @Published var error:    String?

    func load() async {
        error = nil
        do {
            async let o = APIService.shared.getOpenPositions()
            async let p = APIService.shared.getPendingPositions()
            async let h = APIService.shared.getPositionHistory(days: 30)
            async let e = APIService.shared.getExposure()
            open     = try await o
            pending  = try await p
            history  = try await h
            exposure = try await e
        } catch { self.error = error.localizedDescription }
    }

    func approve(_ id: String) async {
        do { try await APIService.shared.approvePosition(id); await load() }
        catch { self.error = error.localizedDescription }
    }

    func reject(_ id: String) async {
        do { try await APIService.shared.rejectPosition(id); await load() }
        catch { self.error = error.localizedDescription }
    }
}
