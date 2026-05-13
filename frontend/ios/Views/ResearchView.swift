import SwiftUI

// MARK: - View

struct ResearchView: View {
    @StateObject private var vm = ResearchVM()
    @State private var segment: Segment = .screener
    @State private var searchTicker = ""
    @State private var selectedMemo: ResearchMemo?
    @State private var showTriggerSheet = false
    @State private var triggerTicker = ""

    enum Segment: String, CaseIterable { case screener = "Screener", memos = "Memos" }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Segment", selection: $segment) {
                    ForEach(Segment.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal).padding(.vertical, 8)

                Group {
                    switch segment {
                    case .screener: ScreenerList(items: vm.watchlist, onRunScreeer: { Task { await vm.runScreener() } })
                    case .memos:    MemoList(memos: vm.memos, onTap: { selectedMemo = $0 })
                    }
                }
                .frame(maxHeight: .infinity)
            }
            .navigationTitle("Research")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showTriggerSheet = true } label: {
                        Label("Run Research", systemImage: "plus.circle")
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button { Task { await vm.load() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .sheet(item: $selectedMemo) { m in MemoDetailSheet(memo: m) }
            .sheet(isPresented: $showTriggerSheet) {
                TriggerResearchSheet(
                    ticker: $triggerTicker,
                    isRunning: vm.triggerRunning,
                    onSubmit: { t in Task { await vm.triggerResearch(t); showTriggerSheet = false } },
                    onCancel: { showTriggerSheet = false }
                )
            }
            .overlay {
                if let err = vm.error { VStack { ErrorCard(message: err).padding(); Spacer() } }
            }
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }
}

// MARK: - Screener List

private struct ScreenerList: View {
    let items: [WatchlistItem]
    let onRunScreeer: () -> Void

    var body: some View {
        if items.isEmpty {
            VStack(spacing: 16) {
                EmptyStateView(icon: "chart.bar.doc.horizontal",
                               title: "No Screener Data",
                               subtitle: "Screener runs daily at 4PM ET")
                Button("Run Now", action: onRunScreeer)
                    .buttonStyle(.borderedProminent)
            }
        } else {
            List(items) { item in
                ScreenerRow(item: item)
            }
            .listStyle(.plain)
        }
    }
}

private struct ScreenerRow: View {
    let item: WatchlistItem

    var body: some View {
        HStack(spacing: 12) {
            // Rank
            Text("#\(item.rank)")
                .font(.caption.bold())
                .foregroundStyle(.secondary)
                .frame(width: 28, alignment: .trailing)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(item.ticker).font(.headline)
                    if let sector = item.sector {
                        Text(sector).font(.caption2).foregroundStyle(.secondary)
                    }
                    if item.insiderSignal == true {
                        Image(systemName: "person.fill.checkmark")
                            .font(.caption2).foregroundStyle(.blue)
                    }
                }
                // Factor score bars
                FactorBars(q: item.qualityScore, v: item.valueScore, m: item.momentumScore)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(String(format: "%.2f", item.compositeScore))
                    .font(.title3.bold())
                    .foregroundStyle(scoreColor(item.compositeScore))
                if let bFlag = item.beneishFlag, bFlag != "CLEAN" {
                    Text(bFlag).font(.caption2).foregroundStyle(.orange)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func scoreColor(_ s: Double) -> Color {
        s >= 8.0 ? .green : s >= 6.5 ? .orange : .red
    }
}

private struct FactorBars: View {
    let q: Double; let v: Double; let m: Double
    var body: some View {
        HStack(spacing: 4) {
            MiniBar(label: "Q", value: q)
            MiniBar(label: "V", value: v)
            MiniBar(label: "M", value: m)
        }
    }
}

private struct MiniBar: View {
    let label: String; let value: Double
    var body: some View {
        HStack(spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(.secondary).frame(width: 10)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.15)).frame(height: 3)
                    Capsule().fill(Color.accentColor.opacity(0.7))
                        .frame(width: g.size.width * min(value / 10, 1), height: 3)
                }
            }
            .frame(width: 40, height: 3)
            Text(String(format: "%.1f", value)).font(.caption2).foregroundStyle(.secondary).frame(width: 22)
        }
    }
}

// MARK: - Memo List

private struct MemoList: View {
    let memos: [ResearchMemo]
    let onTap: (ResearchMemo) -> Void

    var body: some View {
        if memos.isEmpty {
            EmptyStateView(icon: "doc.text", title: "No Memos",
                           subtitle: "Approved & watchlist memos appear here")
        } else {
            List(memos) { memo in
                Button { onTap(memo) } label: { MemoRow(memo: memo) }
                    .buttonStyle(.plain)
            }
            .listStyle(.plain)
        }
    }
}

private struct MemoRow: View {
    let memo: ResearchMemo

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(memo.ticker).font(.headline)
                    VerdictBadge(verdict: memo.verdict)
                    Text(memo.status).font(.caption2).foregroundStyle(.secondary)
                }
                ConvictionBar(score: memo.convictionScore).frame(width: 120)
                if let sector = memo.memoJson?.sector ?? (memo.memoJson?.companyOverview.map { _ in nil } ?? nil) {
                    Text(sector).font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if let pt = memo.memoJson?.priceTarget {
                    Text("PT: $\(String(format: "%.0f", pt))")
                        .font(.caption.bold()).foregroundStyle(.blue)
                }
                if let date = memo.createdAt {
                    Text(date.shortTimestamp).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Memo Detail Sheet

private struct MemoDetailSheet: View {
    let memo: ResearchMemo
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        VerdictBadge(verdict: memo.verdict)
                        ConvictionBar(score: memo.convictionScore)
                        if let pt = memo.memoJson?.priceTarget {
                            Spacer()
                            Text("PT $\(String(format: "%.2f", pt))").font(.subheadline.bold()).foregroundStyle(.blue)
                        }
                    }
                }

                if let overview = memo.memoJson?.companyOverview {
                    Section("Company Overview") { Text(overview).font(.subheadline) }
                }

                if let vp = memo.memoJson?.variantPerception {
                    Section("Variant Perception") {
                        Text(vp).font(.subheadline).italic()
                    }
                }

                if let cat = memo.memoJson?.repricingCatalyst {
                    Section("Repricing Catalyst") { Text(cat).font(.subheadline) }
                }

                if let bulls = memo.memoJson?.bullThesis, !bulls.isEmpty {
                    Section("Bull Thesis") {
                        ForEach(bulls, id: \.self) { point in
                            Label(point, systemImage: "arrow.up.circle.fill")
                                .font(.subheadline)
                                .foregroundStyle(.green)
                        }
                    }
                }

                if let bears = memo.memoJson?.bearThesis, !bears.isEmpty {
                    Section("Bear Thesis") {
                        ForEach(bears, id: \.self) { point in
                            Label(point, systemImage: "arrow.down.circle.fill")
                                .font(.subheadline)
                                .foregroundStyle(.red)
                        }
                    }
                }

                if let risks = memo.memoJson?.keyRisks, !risks.isEmpty {
                    Section("Key Risks") {
                        ForEach(risks, id: \.self) { r in
                            Label(r, systemImage: "exclamationmark.triangle")
                                .font(.subheadline).foregroundStyle(.orange)
                        }
                    }
                }

                if let cats = memo.memoJson?.catalysts, !cats.isEmpty {
                    Section("Catalysts") {
                        ForEach(cats, id: \.self) { c in
                            Label(c, systemImage: "bolt.circle").font(.subheadline)
                        }
                    }
                }

                if let val = memo.memoJson?.valuationNote {
                    Section("Valuation") { Text(val).font(.subheadline) }
                }

                if let rationale = memo.memoJson?.convictionScoreRationale {
                    Section("Conviction Rationale") { Text(rationale).font(.subheadline) }
                }

                if let macro = memo.memoJson?.macroSensitivity {
                    Section("Macro Sensitivity") { Text(macro).font(.subheadline) }
                }

                if let summary = memo.memoJson?.summary {
                    Section("Summary") { Text(summary).font(.subheadline) }
                }
            }
            .navigationTitle(memo.ticker)
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
        }
    }
}

// MARK: - Trigger Research Sheet

private struct TriggerResearchSheet: View {
    @Binding var ticker: String
    let isRunning: Bool
    let onSubmit: (String) -> Void
    let onCancel: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section("Ticker Symbol") {
                    TextField("e.g. AAPL", text: $ticker)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }
                Section {
                    Button(isRunning ? "Running…" : "Run Research") {
                        onSubmit(ticker.uppercased().trimmingCharacters(in: .whitespaces))
                    }
                    .disabled(ticker.trimmingCharacters(in: .whitespaces).isEmpty || isRunning)
                    .frame(maxWidth: .infinity)
                }
                Section {
                    Text("Triggers the full research pipeline: SEC filings, transcripts, news → LLM memo. Takes ~60 seconds.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Research Ticker")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel", action: onCancel) }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - ViewModel

@MainActor
final class ResearchVM: ObservableObject {
    @Published var watchlist:      [WatchlistItem] = []
    @Published var memos:          [ResearchMemo]  = []
    @Published var triggerRunning  = false
    @Published var error:          String?

    func load() async {
        error = nil
        do {
            async let w = APIService.shared.getScreenerWatchlist()
            async let m = APIService.shared.getResearchWatchlist()
            watchlist = try await w
            memos     = try await m
        } catch { self.error = error.localizedDescription }
    }

    func runScreener() async {
        do { try await APIService.shared.triggerScreening(); await load() }
        catch { self.error = error.localizedDescription }
    }

    func triggerResearch(_ ticker: String) async {
        triggerRunning = true; defer { triggerRunning = false }
        do { try await APIService.shared.triggerResearch(ticker: ticker); await load() }
        catch { self.error = error.localizedDescription }
    }
}
