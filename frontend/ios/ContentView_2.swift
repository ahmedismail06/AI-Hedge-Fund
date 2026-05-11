//
//  ContentView.swift
//  TradingDashboard
//
//  Main navigation container for the app
//

import SwiftUI

struct ContentView: View {
    @State private var selectedTab: NavigationTab = .dashboard
    
    var body: some View {
        TabView(selection: $selectedTab) {
            DashboardView()
                .tabItem {
                    Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis")
                }
                .tag(NavigationTab.dashboard)
            
            PortfolioView()
                .tabItem {
                    Label("Portfolio", systemImage: "briefcase.fill")
                }
                .tag(NavigationTab.portfolio)
            
            ExecutionView()
                .tabItem {
                    Label("Execution", systemImage: "arrow.left.arrow.right")
                }
                .tag(NavigationTab.execution)
            
            ResearchView()
                .tabItem {
                    Label("Research", systemImage: "doc.text.magnifyingglass")
                }
                .tag(NavigationTab.research)
            
            MoreView()
                .tabItem {
                    Label("More", systemImage: "ellipsis")
                }
                .tag(NavigationTab.more)
        }
    }
}

enum NavigationTab {
    case dashboard
    case portfolio
    case execution
    case research
    case more
}

#Preview {
    ContentView()
        .environmentObject(ThemeManager())
}
