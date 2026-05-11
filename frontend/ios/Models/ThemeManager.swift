//
//  ThemeManager.swift
//  TradingDashboard
//
//  Manages app-wide theme settings (light/dark mode, accent colors)
//

import SwiftUI

class ThemeManager: ObservableObject {
    @Published var isDarkMode: Bool = false
    @Published var accentColor: Color = .blue
    
    enum AppTheme {
        case light
        case dark
        case system
    }
    
    @Published var currentTheme: AppTheme = .system {
        didSet {
            applyTheme()
        }
    }
    
    init() {
        applyTheme()
    }
    
    private func applyTheme() {
        switch currentTheme {
        case .light:
            isDarkMode = false
        case .dark:
            isDarkMode = true
        case .system:
            // System will handle this automatically
            break
        }
    }
}
