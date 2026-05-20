import SwiftUI

struct ContentView: View {
    @EnvironmentObject var themeManager: ThemeManager
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis") }
                .tag(0)
            PortfolioView()
                .tabItem { Label("Portfolio", systemImage: "briefcase.fill") }
                .tag(1)
            DecisionsView()
                .tabItem { Label("Decisions", systemImage: "brain.head.profile") }
                .tag(2)
            ResearchView()
                .tabItem { Label("Research", systemImage: "doc.text.magnifyingglass") }
                .tag(3)
            RiskView()
                .tabItem { Label("Risk", systemImage: "exclamationmark.shield.fill") }
                .tag(4)
        }
        .tint(themeManager.accentColor)
    }
}

#Preview {
    ContentView().environmentObject(ThemeManager())
}
