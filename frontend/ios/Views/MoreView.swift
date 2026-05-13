import SwiftUI
import Charts

// MARK: - View (struct: RiskView)

struct RiskView: View {
    @StateObject private var vm = RiskVM()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if let err = vm.error { ErrorCard(message: err) }

                    if let briefing = vm.briefing { MacroCard(briefing: briefing) }

                    if let metrics = vm.metrics { MetricsCard(metrics: metrics) }

                    AlertsSection(alerts: vm.alerts,
                                  onResolve: { id in Task { await vm.resolveAlert(id) } })
                }
                .padding()
            }
            .navigationTitle("Risk & Macro")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { Task { await vm.load() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }
}

// MARK: - Macro Card

private struct MacroCard: View {
    let briefing: MacroBriefing

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                RegimeBadge(regime: briefing.regime)
                Text(String(format: "Score: %.0f / 100", briefing.regimeScore))
                    .font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                if briefing.regimeChanged, let prev = briefing.previousRegime {
                    HStack(spacing: 4) {
                        Text(prev).font(.caption2).foregroundStyle(.secondary)
                        Image(systemName: "arrow.right").font(.caption2)
                        Text(briefing.regime).font(.caption2.bold())
                    }
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Color.orange.opacity(0.15), in: Capsule())
                }
            }

            Text(briefing.qualitativeSummary)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            if !briefing.keyThemes.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 4) {
                    Text("Key Themes").font(.caption.bold()).foregroundStyle(.secondary)
                    ForEach(briefing.keyThemes, id: \.self) { theme in
                        Label(theme, systemImage: "circle.fill")
                            .font(.caption)
                            .symbolRenderingMode(.hierarchical)
                    }
                }
            }

            Divider()
            Text(briefing.portfolioGuidance)
                .font(.caption.italic())
                .foregroundStyle(.secondary)

            if let indicators = briefing.indicatorScores, !indicators.isEmpty {
                Divider()
                IndicatorGrid(indicators: indicators)
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct IndicatorGrid: View {
    let indicators: [IndicatorScore]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Indicators").font(.caption.bold()).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 6) {
                ForEach(indicators.prefix(8)) { ind in
                    IndicatorCell(indicator: ind)
                }
            }
        }
    }
}

private struct IndicatorCell: View {
    let indicator: IndicatorScore
    var signalColor: Color {
        switch indicator.signal {
        case "bullish": return .green
        case "bearish": return .red
        default:        return .secondary
        }
    }
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(signalColor).frame(width: 6, height: 6)
            VStack(alignment: .leading, spacing: 1) {
                Text(indicator.name).font(.caption2).lineLimit(1)
                Text(String(format: "%.2f", indicator.value)).font(.caption.bold())
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(signalColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Metrics Card

private struct MetricsCard: View {
    let metrics: PortfolioMetrics

    private func fmt(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.2f", v)
    }
    private func pctFmt(_ v: Double?) -> String {
        guard let v else { return "—" }
        return String(format: "%.1f%%", v * 100)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Portfolio Metrics").font(.headline)
                Spacer()
                if let date = metrics.date {
                    Text(date).font(.caption).foregroundStyle(.secondary)
                }
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 10) {
                MetricTile(label: "Sharpe",    value: fmt(metrics.sharpeRatio),
                           valueColor: (metrics.sharpeRatio ?? 0) >= 1.0 ? .green : .orange)
                MetricTile(label: "Sortino",   value: fmt(metrics.sortinoRatio),
                           valueColor: (metrics.sortinoRatio ?? 0) >= 1.5 ? .green : .orange)
                MetricTile(label: "Calmar",    value: fmt(metrics.calmarRatio))
                MetricTile(label: "VaR 95%",   value: pctFmt(metrics.var95),
                           valueColor: .orange)
                MetricTile(label: "Max DD",    value: pctFmt(metrics.maxDrawdown),
                           valueColor: .red)
                MetricTile(label: "Beta",      value: fmt(metrics.beta))
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: - Alerts Section

private struct AlertsSection: View {
    let alerts: [RiskAlert]
    let onResolve: (String) -> Void

    var unresolved: [RiskAlert] { alerts.filter { !$0.resolved } }
    var resolved:   [RiskAlert] { alerts.filter { $0.resolved } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Risk Alerts").font(.headline)
                if !unresolved.isEmpty {
                    Text("\(unresolved.count) active")
                        .font(.caption.bold()).foregroundStyle(.white)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(.red, in: Capsule())
                }
                Spacer()
            }

            if alerts.isEmpty {
                EmptyStateView(icon: "checkmark.shield", title: "No Alerts",
                               subtitle: "Risk monitor is clean")
            } else {
                ForEach(unresolved) { alert in
                    AlertRow(alert: alert, onResolve: { onResolve(alert.id) })
                }
                if !resolved.isEmpty {
                    DisclosureGroup("Resolved (\(resolved.count))") {
                        ForEach(resolved.prefix(10)) { alert in
                            AlertRow(alert: alert, onResolve: nil)
                        }
                    }
                    .font(.subheadline)
                }
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct AlertRow: View {
    let alert: RiskAlert
    let onResolve: (() -> Void)?

    var tierLabel: String {
        switch alert.tier {
        case 1: return "Position"
        case 2: return "Strategy"
        case 3: return "Portfolio"
        default: return "Tier \(alert.tier)"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                SeverityBadge(severity: alert.severity)
                Text(tierLabel).font(.caption2).foregroundStyle(.secondary)
                if let ticker = alert.ticker { Text(ticker).font(.caption.bold()) }
                Spacer()
                RegimeBadge(regime: alert.regime)
            }
            Text(alert.trigger).font(.subheadline)
            HStack {
                if let ts = alert.createdAt {
                    Text(ts.shortTimestamp).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                if let resolve = onResolve {
                    Button("Resolve") { resolve() }
                        .font(.caption.bold()).buttonStyle(.bordered).tint(.green).controlSize(.small)
                }
            }
        }
        .padding(10)
        .background(alert.resolved ? Color.clear : Color(.systemBackground),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(severityBorderColor, lineWidth: alert.resolved ? 0 : 1))
        .opacity(alert.resolved ? 0.5 : 1.0)
    }

    private var severityBorderColor: Color {
        switch alert.severity {
        case "CRITICAL": return .red.opacity(0.5)
        case "BREACH":   return .orange.opacity(0.4)
        default:         return .yellow.opacity(0.3)
        }
    }
}

// MARK: - ViewModel

@MainActor
final class RiskVM: ObservableObject {
    @Published var briefing: MacroBriefing?
    @Published var metrics:  PortfolioMetrics?
    @Published var alerts:   [RiskAlert] = []
    @Published var error:    String?

    func load() async {
        error = nil
        do {
            async let b = APIService.shared.getMacroBriefing()
            async let m = APIService.shared.getRiskMetrics()
            async let a = APIService.shared.getRiskAlerts()
            briefing = try await b
            metrics  = try? await m   // may return 404 if no metrics yet
            alerts   = try await a
        } catch { self.error = error.localizedDescription }
    }

    func resolveAlert(_ id: String) async {
        do { try await APIService.shared.resolveAlert(id); await load() }
        catch { self.error = error.localizedDescription }
    }
}
