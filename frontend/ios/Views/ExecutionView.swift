import SwiftUI

// MARK: - View (struct name kept as DecisionsView — tab shows "Decisions")

struct DecisionsView: View {
    @StateObject private var vm = DecisionsVM()
    @State private var selectedCategory: String? = nil
    @State private var selectedDecision: PMDecision?

    private let categories = ["NEW_ENTRY", "EXIT_TRIM", "REBALANCE", "PRE_EARNINGS", "CRISIS"]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Category filter strip
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        FilterChip(label: "All", selected: selectedCategory == nil) {
                            selectedCategory = nil
                        }
                        ForEach(categories, id: \.self) { cat in
                            FilterChip(label: CategoryBadge(category: cat).label,
                                       selected: selectedCategory == cat) {
                                selectedCategory = selectedCategory == cat ? nil : cat
                            }
                        }
                    }
                    .padding(.horizontal).padding(.vertical, 8)
                }
                .background(Color(.systemGroupedBackground))

                Group {
                    if vm.isLoading && vm.decisions.isEmpty {
                        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if let err = vm.error {
                        VStack { ErrorCard(message: err).padding(); Spacer() }
                    } else {
                        let filtered = vm.decisions.filter { d in
                            guard let cat = selectedCategory else { return true }
                            return d.category == cat
                        }
                        if filtered.isEmpty {
                            EmptyStateView(icon: "brain.head.profile",
                                           title: "No Decisions",
                                           subtitle: "The PM agent hasn't fired yet, or no decisions match this filter")
                        } else {
                            List(filtered) { d in
                                Button { selectedDecision = d } label: { DecisionCard(decision: d) }
                                    .buttonStyle(.plain)
                            }
                            .listStyle(.plain)
                        }
                    }
                }
                .frame(maxHeight: .infinity)
            }
            .navigationTitle("PM Decisions")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await vm.load() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .sheet(item: $selectedDecision) { d in
                DecisionDetailSheet(decision: d, onOverride: { type, reason, deferUntil in
                    Task {
                        await vm.override(id: d.decisionId, type: type,
                                          reason: reason, deferUntil: deferUntil)
                    }
                })
            }
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }
}

// MARK: - Filter Chip

private struct FilterChip: View {
    let label: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.caption.bold())
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(selected ? Color.accentColor : Color.secondary.opacity(0.15),
                            in: Capsule())
                .foregroundStyle(selected ? .white : .primary)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Decision Card

private struct DecisionCard: View {
    let decision: PMDecision

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                CategoryBadge(category: decision.category)
                if let t = decision.ticker { Text(t).font(.subheadline.bold()) }
                Spacer()
                ExecutionStatusBadge(status: decision.executionStatus)
            }

            Text(decision.decision)
                .font(.subheadline)
                .lineLimit(2)

            HStack {
                if let conf = decision.confidence {
                    Label(String(format: "%.0f%%", conf * 100), systemImage: "brain")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(decision.timestamp.shortTimestamp)
                    .font(.caption).foregroundStyle(.secondary)
            }

            if decision.humanOverride != nil {
                Label("Human Override Applied", systemImage: "person.badge.shield.checkmark")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 6)
    }
}

// MARK: - Decision Detail Sheet

private struct DecisionDetailSheet: View {
    let decision: PMDecision
    let onOverride: (String, String, String?) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var showOverrideSheet = false
    @State private var overrideType = "BLOCK"
    @State private var overrideReason = ""
    @State private var deferUntil = ""

    var body: some View {
        NavigationStack {
            List {
                Section("Summary") {
                    detailRow("Category",   decision.category)
                    detailRow("Ticker",     decision.ticker ?? "—")
                    detailRow("Decision",   decision.decision)
                    detailRow("Status",     decision.executionStatus)
                    if let c = decision.confidence {
                        detailRow("Confidence", String(format: "%.0f%%", c * 100))
                    }
                    detailRow("Timestamp",  decision.timestamp.shortTimestamp)
                }

                Section("Reasoning") {
                    Text(decision.reasoning)
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                }

                if let ra = decision.riskAssessment, !ra.isEmpty {
                    Section("Risk Assessment") {
                        Text(ra).font(.subheadline).foregroundStyle(.secondary)
                    }
                }

                if let ad = decision.actionDetails, !ad.isEmpty {
                    Section("Action Details") {
                        ForEach(ad.sorted(by: { $0.key < $1.key }), id: \.key) { k, v in
                            detailRow(k.replacingOccurrences(of: "_", with: " ").capitalized,
                                      v.description)
                        }
                    }
                }

                if let ho = decision.humanOverride {
                    Section("Human Override") {
                        if let type = ho["override_type"] { detailRow("Type",   type.description) }
                        if let reason = ho["reason"]      { detailRow("Reason", reason.description) }
                    }
                }

                if ["PENDING_HUMAN", "SENT_TO_EXECUTION"].contains(decision.executionStatus) {
                    Section("Override") {
                        Button("Override This Decision") { showOverrideSheet = true }
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle("Decision \(decision.decisionId)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
            .sheet(isPresented: $showOverrideSheet) {
                OverrideSheet(
                    overrideType: $overrideType,
                    reason: $overrideReason,
                    deferUntil: $deferUntil,
                    onSubmit: {
                        let du = overrideType == "DEFER" && !deferUntil.isEmpty ? deferUntil : nil
                        onOverride(overrideType, overrideReason, du)
                        showOverrideSheet = false
                        dismiss()
                    },
                    onCancel: { showOverrideSheet = false }
                )
            }
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).bold().multilineTextAlignment(.trailing).lineLimit(3)
        }
    }
}

// MARK: - Override Sheet

private struct OverrideSheet: View {
    @Binding var overrideType: String
    @Binding var reason: String
    @Binding var deferUntil: String
    let onSubmit: () -> Void
    let onCancel: () -> Void

    private let types = ["BLOCK", "FORCE_EXECUTE", "DEFER"]

    var body: some View {
        NavigationStack {
            Form {
                Section("Override Type") {
                    Picker("Type", selection: $overrideType) {
                        ForEach(types, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")) }
                    }
                    .pickerStyle(.segmented)
                }

                Section("Reason") {
                    TextField("Why are you overriding?", text: $reason, axis: .vertical)
                        .lineLimit(3...6)
                }

                if overrideType == "DEFER" {
                    Section("Defer Until") {
                        TextField("YYYY-MM-DD or e.g. '7 days'", text: $deferUntil)
                    }
                }

                Section {
                    Button("Submit Override") { onSubmit() }
                        .disabled(reason.trimmingCharacters(in: .whitespaces).isEmpty)
                        .frame(maxWidth: .infinity)
                        .foregroundStyle(reason.isEmpty ? Color.secondary : Color.orange)
                }
            }
            .navigationTitle("Override Decision")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { onCancel() } }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

// MARK: - ViewModel

@MainActor
final class DecisionsVM: ObservableObject {
    @Published var decisions: [PMDecision] = []
    @Published var isLoading = false
    @Published var error: String?

    func load() async {
        isLoading = true; error = nil
        defer { isLoading = false }
        do { decisions = try await APIService.shared.getDecisions(limit: 100) }
        catch { self.error = error.localizedDescription }
    }

    func override(id: String, type: String, reason: String, deferUntil: String?) async {
        do {
            try await APIService.shared.overrideDecision(id: id, type: type, reason: reason,
                                                          deferUntil: deferUntil)
            await load()
        } catch { self.error = error.localizedDescription }
    }
}
