import SwiftUI

// MARK: - View

struct DashboardView: View {
    @StateObject private var vm = DashboardVM()

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading && vm.status == nil {
                    ProgressView("Loading…").frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        VStack(spacing: 16) {
                            if let err = vm.error { ErrorCard(message: err) }
                            if let s = vm.status {
                                PMStatusCard(status: s, onHalt: { Task { await vm.halt() } },
                                             onResume: { Task { await vm.resume() } })
                                PortfolioStatsRow(p: s.portfolio)
                                if let last = s.lastCycle { LastCycleCard(cycle: last) }
                            }
                            if !vm.decisions.isEmpty {
                                RecentDecisionsSection(decisions: vm.decisions)
                            }
                        }
                        .padding()
                    }
                }
            }
            .navigationTitle("AI Hedge Fund")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await vm.runCycle() } } label: {
                        Label("Run Cycle", systemImage: "play.circle")
                    }
                    .disabled(vm.cycleRunning)
                }
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

// MARK: - PM Status Card

private struct PMStatusCard: View {
    let status: PMStatus
    let onHalt: () -> Void
    let onResume: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("AI Portfolio Manager").font(.headline)
                    HStack(spacing: 8) {
                        ModeBadge(mode: status.mode)
                        if status.dailyLossHaltTriggered {
                            Label("HALTED", systemImage: "pause.fill")
                                .font(.caption.bold()).foregroundStyle(.white)
                                .padding(.horizontal, 8).padding(.vertical, 3)
                                .background(.red, in: Capsule())
                        }
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 6) {
                    if status.activeCriticalAlerts > 0 {
                        Label("\(status.activeCriticalAlerts) CRITICAL", systemImage: "exclamationmark.triangle.fill")
                            .font(.caption.bold()).foregroundStyle(.white)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(.red, in: Capsule())
                    }
                    Text("\(status.decisionsToday) decisions today")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Divider()

            HStack {
                Text("Cycle every \(status.cycleIntervalSeconds / 60) min")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                if status.dailyLossHaltTriggered {
                    Button("Resume", action: onResume)
                        .buttonStyle(.borderedProminent).tint(.green).controlSize(.small)
                } else {
                    Button("Halt All Entries", action: onHalt)
                        .buttonStyle(.bordered).tint(.red).controlSize(.small)
                }
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: - Portfolio Stats

private struct PortfolioStatsRow: View {
    let p: PortfolioSummary

    var body: some View {
        HStack(spacing: 0) {
            StatCell(label: "Positions", value: "\(p.positionCount)")
            divider
            StatCell(label: "Gross Exp", value: (p.grossExposure * 100).pctString)
            divider
            StatCell(label: "Net Exp",   value: (p.netExposure * 100).pctString)
            divider
            StatCell(label: "Cash",      value: (p.cashPct * 100).pctString)
        }
        .padding(.vertical, 8)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var divider: some View {
        Divider().frame(height: 36)
    }
}

private struct StatCell: View {
    let label: String
    let value: String
    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(.title3.bold())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Last Cycle Card

private struct LastCycleCard: View {
    let cycle: LastCycleInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Last Cycle", systemImage: "clock.arrow.circlepath")
                .font(.subheadline.bold()).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                if let cat = cycle.category { CategoryBadge(category: cat) }
                if let dec = cycle.decision {
                    Text(dec).font(.subheadline).lineLimit(1)
                }
                Spacer()
                if let ts = cycle.timestamp {
                    Text(ts.shortTimestamp).font(.caption).foregroundStyle(.secondary)
                }
            }
            if let es = cycle.executionStatus { ExecutionStatusBadge(status: es) }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: - Recent Decisions

private struct RecentDecisionsSection: View {
    let decisions: [PMDecision]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recent Decisions").font(.headline)
            ForEach(decisions.prefix(6)) { d in
                HStack(spacing: 10) {
                    CategoryBadge(category: d.category)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            if let t = d.ticker { Text(t).font(.subheadline.bold()) }
                            Text(d.decision).font(.subheadline).lineLimit(1)
                        }
                        Text(d.timestamp.shortTimestamp).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    ExecutionStatusBadge(status: d.executionStatus)
                }
                .padding(.vertical, 6).padding(.horizontal, 10)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }
}

// MARK: - ViewModel

@MainActor
final class DashboardVM: ObservableObject {
    @Published var status: PMStatus?
    @Published var decisions: [PMDecision] = []
    @Published var isLoading = false
    @Published var cycleRunning = false
    @Published var error: String?

    func load() async {
        isLoading = true; error = nil
        defer { isLoading = false }
        do {
            async let s = APIService.shared.getPMStatus()
            async let d = APIService.shared.getDecisions(limit: 8)
            status    = try await s
            decisions = try await d
        } catch { self.error = error.localizedDescription }
    }

    func halt() async {
        do { try await APIService.shared.haltPM(); await load() }
        catch { self.error = error.localizedDescription }
    }

    func resume() async {
        do { try await APIService.shared.resumePM(); await load() }
        catch { self.error = error.localizedDescription }
    }

    func runCycle() async {
        cycleRunning = true; defer { cycleRunning = false }
        do { try await APIService.shared.triggerCycle(); await load() }
        catch { self.error = error.localizedDescription }
    }
}
