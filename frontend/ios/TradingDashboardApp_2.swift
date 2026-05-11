//
//  TradingDashboardApp.swift
//  TradingDashboard
//
//  Main app entry point for iOS Trading Dashboard
//

import SwiftUI

@main
struct TradingDashboardApp: App {
    @StateObject private var themeManager = ThemeManager()
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(themeManager)
        }
    }
}
