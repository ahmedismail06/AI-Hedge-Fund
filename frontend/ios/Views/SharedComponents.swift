import SwiftUI

// MARK: - Regime Badge

struct RegimeBadge: View {
    let regime: String
    var color: Color {
        switch regime {
        case "Risk-On":       return .green
        case "Risk-Off":      return .red
        case "Transitional":  return .orange
        case "Stagflation":   return .purple
        default:              return .gray
        }
    }
    var body: some View {
        Text(regime)
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color, in: Capsule())
    }
}

// MARK: - PM Mode Badge

struct ModeBadge: View {
    let mode: String
    var body: some View {
        Text(mode.uppercased())
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(mode == "autonomous" ? Color.blue : Color.secondary, in: Capsule())
    }
}

// MARK: - Decision Category Badge

struct CategoryBadge: View {
    let category: String
    var color: Color {
        switch category {
        case "NEW_ENTRY":    return .green
        case "EXIT_TRIM":    return .orange
        case "REBALANCE":    return .blue
        case "CRISIS":       return .red
        case "PRE_EARNINGS": return .purple
        default:             return .gray
        }
    }
    var label: String {
        switch category {
        case "NEW_ENTRY":    return "Entry"
        case "EXIT_TRIM":    return "Exit"
        case "REBALANCE":    return "Rebal"
        case "CRISIS":       return "Crisis"
        case "PRE_EARNINGS": return "Earnings"
        default:             return category
        }
    }
    var body: some View {
        Text(label)
            .font(.caption2.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(color, in: Capsule())
    }
}

// MARK: - Execution Status

struct ExecutionStatusBadge: View {
    let status: String
    var color: Color {
        switch status {
        case "SENT_TO_EXECUTION":  return .green
        case "BLOCKED":            return .red
        case "DEFERRED":           return .orange
        case "NO_ACTION":          return .secondary
        case "PENDING_HUMAN":      return .yellow
        case "TRIGGERED_PIPELINE": return .blue
        default:                   return .secondary
        }
    }
    var body: some View {
        Text(status.replacingOccurrences(of: "_", with: " "))
            .font(.caption2)
            .foregroundStyle(color)
    }
}

// MARK: - Direction Badge (LONG / SHORT)

struct DirectionBadge: View {
    let direction: String
    var body: some View {
        Text(direction)
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(direction == "LONG" ? Color.green : Color.red, in: Capsule())
    }
}

// MARK: - Verdict Badge

struct VerdictBadge: View {
    let verdict: String
    var color: Color {
        switch verdict {
        case "LONG":  return .green
        case "SHORT": return .red
        case "AVOID": return .secondary
        default:      return .gray
        }
    }
    var body: some View {
        Text(verdict)
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color, in: Capsule())
    }
}

// MARK: - Severity Badge

struct SeverityBadge: View {
    let severity: String
    var color: Color {
        switch severity {
        case "CRITICAL": return .red
        case "BREACH":   return .orange
        case "WARN":     return .yellow
        default:         return .gray
        }
    }
    var body: some View {
        Text(severity)
            .font(.caption2.bold())
            .foregroundStyle(color == .yellow ? .black : .white)
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(color, in: Capsule())
    }
}

// MARK: - Conviction Bar

struct ConvictionBar: View {
    let score: Double  // 0-10
    var color: Color {
        score >= 7.5 ? .green : score >= 5.0 ? .orange : .red
    }
    var body: some View {
        HStack(spacing: 4) {
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.2))
                        .frame(height: 4)
                    Capsule().fill(color)
                        .frame(width: g.size.width * score / 10, height: 4)
                }
            }
            .frame(height: 4)
            Text(String(format: "%.1f", score))
                .font(.caption2.bold())
                .foregroundStyle(color)
                .frame(width: 24, alignment: .trailing)
        }
    }
}

// MARK: - Metric Tile

struct MetricTile: View {
    let label: String
    let value: String
    var valueColor: Color = .primary

    var body: some View {
        VStack(spacing: 3) {
            Text(value)
                .font(.title3.bold())
                .foregroundStyle(valueColor)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Error / Empty States

struct ErrorCard: View {
    let message: String
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
            Text(message).font(.subheadline)
        }
        .foregroundStyle(.red)
        .padding()
        .frame(maxWidth: .infinity)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

struct EmptyStateView: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 40))
                .foregroundStyle(.secondary)
            Text(title).font(.headline)
            Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 50)
    }
}

// MARK: - String helpers

extension String {
    var shortTimestamp: String {
        let formatters: [ISO8601DateFormatter] = {
            let f1 = ISO8601DateFormatter(); f1.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            let f2 = ISO8601DateFormatter(); f2.formatOptions = [.withInternetDateTime]
            return [f1, f2]
        }()
        guard let date = formatters.lazy.compactMap({ $0.date(from: self) }).first else {
            return String(self.prefix(16))
        }
        let f = DateFormatter(); f.dateFormat = "MMM d, HH:mm"
        return f.string(from: date)
    }

    var pnlColor: Color { hasPrefix("-") ? .red : .green }
}

extension Double {
    var pctString: String  { String(format: "%.1f%%", self) }
    var dollarString: String {
        let abs = Swift.abs(self)
        let sign = self < 0 ? "-" : "+"
        if abs >= 1_000_000 { return "\(sign)$\(String(format: "%.1fM", abs / 1_000_000.0))" }
        if abs >= 1_000     { return "\(sign)$\(String(format: "%.1fK", abs / 1_000.0))" }
        return "\(sign)$\(String(format: "%.0f", abs))"
    }
    var pnlColor: Color { self >= 0 ? .green : .red }
}
