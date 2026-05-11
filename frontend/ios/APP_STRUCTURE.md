# App Structure Visual Guide

## 📱 App Navigation Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    TradingDashboardApp                      │
│                    (@main entry point)                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                     ContentView                             │
│                   (TabView - 5 tabs)                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┬─────────────┬───────────┐
        ▼             ▼             ▼             ▼           ▼
   ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌──────┐
   │Dashboard│  │Portfolio │  │Execution │  │Research │  │ More │
   └─────────┘  └──────────┘  └──────────┘  └─────────┘  └──────┘
```

## 🏗️ Detailed View Hierarchy

### Tab 1: Dashboard 📊
```
DashboardView
├── AccountSummaryCard
│   ├── Total Value
│   ├── Gain/Loss
│   └── Day P&L, Buying Power, Cash
├── PortfolioPerformanceChart
│   └── Swift Charts (Line + Area)
├── TimeframeSelector
│   └── 1D, 1W, 1M, 3M, 1Y, ALL
├── QuickStatsGrid
│   ├── Win Rate Card
│   ├── Positions Card
│   ├── Avg Return Card
│   └── Sharpe Ratio Card
├── TopMoversSection
│   └── TopMoverRow (x3)
└── RecentActivitySection
    └── ActivityRow (x3)
```

### Tab 2: Portfolio 💼
```
PortfolioView
├── PortfolioHeaderView
│   ├── Total Value & Gain/Loss
│   └── Day P&L, Cost Basis, Cash
├── SearchBar
├── SortMenu (Value/Performance/Alphabetical)
└── List
    └── PositionRow (multiple)
        └── NavigationLink → PositionDetailView
                            ├── Position Details Section
                            └── Actions Section (Buy/Sell)
```

### Tab 3: Execution ⚡
```
ExecutionView
├── SegmentedControl
│   ├── Orders
│   ├── History
│   └── Fills
├── OrdersListView
│   └── OrderRow (with swipe actions)
├── OrderHistoryView
│   └── TradeHistoryRow
├── FillsListView
│   └── FillRow
└── NewOrderSheet (Sheet)
    ├── Symbol Input
    ├── Side Picker (Buy/Sell)
    ├── Order Type Picker
    ├── Quantity Input
    ├── Price Inputs (conditional)
    └── Submit Button
```

### Tab 4: Research 🔍
```
ResearchView
├── Category Scroll
│   └── CategoryChip (All/Stocks/Earnings/News/Analysts)
├── SearchBar
└── List
    ├── Market Overview Section
    │   └── IndexRow (SPX, DJI, IXIC)
    ├── Watchlist Section
    │   └── WatchlistRow → StockDetailView
    │                       ├── Stock Header
    │                       ├── Tab Picker
    │                       ├── OverviewTab
    │                       ├── ChartTab
    │                       ├── NewsTab
    │                       └── StatsTab
    ├── Recent Research Section
    │   └── ResearchItemRow
    └── Earnings Calendar Section
        └── EarningsRow
```

### Tab 5: More ➕
```
MoreView (NavigationStack)
├── Trading Tools Section
│   ├── Screener → ScreenerView
│   │              ├── Filters Section
│   │              ├── Quick Presets
│   │              ├── Run Button
│   │              └── Results List
│   ├── Macro → MacroView
│   │          ├── Economic Indicators
│   │          ├── Market Sentiment
│   │          └── Global Markets
│   ├── Risk → RiskView
│   │         ├── Risk Score Card (Circular gauge)
│   │         ├── Risk Metrics
│   │         └── Concentration Risk
│   └── Orchestrator → OrchestratorView
│                     ├── Active Strategies
│                     └── Performance Summary
├── Account Section
│   ├── Settings → SettingsView
│   │             ├── Appearance
│   │             ├── Notifications
│   │             └── Data Management
│   └── Profile → ProfileView
│                 ├── User Info
│                 └── Account Stats
└── Support Section
    ├── Help & Support
    └── About
```

## 🔄 Data Flow

```
┌──────────────┐
│   UI Layer   │  (SwiftUI Views)
│  @State      │  - DashboardView
│  @Binding    │  - PortfolioView
│              │  - etc.
└──────┬───────┘
       │ ObservedObject
       ▼
┌──────────────┐
│  Manager     │  (Observable Objects)
│  Layer       │  - ThemeManager
│              │  - (Add ViewModels here)
└──────┬───────┘
       │ async/await
       ▼
┌──────────────┐
│  Service     │  (Business Logic)
│  Layer       │  - APIService
│              │  - (Add more services)
└──────┬───────┘
       │ URLSession
       ▼
┌──────────────┐
│   Network    │  (External)
│   Backend    │  - REST API
│   API        │  - WebSocket (future)
└──────────────┘
```

## 🎨 Theme System

```
ThemeManager (@ObservableObject)
├── currentTheme: AppTheme
│   ├── .system → Follow device
│   ├── .light  → Force light
│   └── .dark   → Force dark
├── isDarkMode: Bool
└── accentColor: Color

Injected via EnvironmentObject
↓
Available in all views
```

## 📦 Component Reusability

### Shared Components
```
┌─────────────────────────────┐
│  Reusable Components        │
├─────────────────────────────┤
│ • MenuRow                   │  Used in: MoreView
│ • CategoryChip              │  Used in: ResearchView, ScreenerView
│ • DetailRow                 │  Used in: PortfolioView, PositionDetailView
│ • StatColumn                │  Used in: DashboardView, PortfolioView
│ • KeyStatItem               │  Used in: ResearchView
│ • StatRow                   │  Used in: ResearchView, RiskView
└─────────────────────────────┘
```

## 🗂️ File Organization

```
frontend_swift/
│
├── 📄 Documentation
│   ├── README.md             ← Start here
│   ├── QUICKSTART.md         ← Setup guide
│   ├── PROJECT_CONFIG.md     ← Configuration
│   ├── OVERVIEW.md           ← Feature summary
│   └── APP_STRUCTURE.md      ← This file
│
├── 🎯 Entry Point
│   ├── TradingDashboardApp.swift    ← @main
│   └── ContentView.swift             ← TabView
│
├── 📁 Views/ (Main Features)
│   ├── DashboardView.swift           ← Tab 1
│   ├── PortfolioView.swift           ← Tab 2
│   ├── ExecutionView.swift           ← Tab 3
│   ├── ResearchView.swift            ← Tab 4
│   └── MoreView.swift                ← Tab 5
│
├── 📁 Managers/ (State Management)
│   └── ThemeManager.swift
│
├── 📁 Services/ (Business Logic)
│   └── APIService.swift
│
└── 📁 Config/ (Configuration)
    └── AppConfig.swift
```

## 🎯 State Management Strategy

```
View Level (@State)
├── Local UI state
├── Form inputs
├── Toggle states
└── Temporary values

Manager Level (@StateObject/@ObservedObject)
├── Shared app state
├── Theme preferences
├── User session
└── Settings

Service Level (async functions)
├── Network requests
├── Data fetching
├── Business logic
└── API communication
```

## 🔗 Navigation Patterns

### TabView Navigation
```
ContentView (TabView)
└── Tab Selection (selectedTab)
    ├── Dashboard    (index 0)
    ├── Portfolio    (index 1)
    ├── Execution    (index 2)
    ├── Research     (index 3)
    └── More         (index 4)
```

### NavigationStack (in each tab)
```
NavigationStack
├── Root View
└── NavigationLink
    └── Detail View
        └── NavigationLink
            └── Deeper View
```

### Sheet/Modal Presentation
```
View
├── @State var showSheet = false
└── .sheet(isPresented: $showSheet)
    └── Modal View
```

## 📊 Chart Integration

```
DashboardView
└── PortfolioPerformanceChart
    └── Swift Charts
        ├── LineMark (Price line)
        │   ├── x: Date
        │   ├── y: Value
        │   └── interpolation: catmullRom
        └── AreaMark (Filled area)
            ├── foregroundStyle: gradient
            └── opacity: 0.1
```

## 🎨 Styling System

```
AppConfig.UI
├── animationDuration: 0.3s
├── cardCornerRadius: 12
├── cardShadowRadius: 5
├── defaultPadding: 16
└── Colors
    ├── profitColor: .green
    ├── lossColor: .red
    └── accentColor: .blue

Applied via:
.cornerRadius(AppConfig.UI.cardCornerRadius)
.shadow(radius: AppConfig.UI.cardShadowRadius)
```

## 🔄 API Request Flow

```
1. User Action
   ├── Button tap
   ├── View appear
   └── Pull to refresh

2. Async Call
   └── Task {
       └── APIService.shared.request<T>()
   }

3. Network Request
   ├── Build URLRequest
   ├── Add headers
   ├── Add auth token
   └── URLSession.data()

4. Response Handling
   ├── Check status code
   ├── Decode JSON
   ├── Update @State
   └── Show in UI

5. Error Handling
   ├── Catch APIError
   ├── Show error UI
   └── Log for debugging
```

## 🎯 Feature Flags

```
AppConfig.Features
├── enableRealTimeData: false    → WebSocket support
├── enablePushNotifications: true → APNS
├── enableBiometricAuth: true    → Face/Touch ID
├── enableWidgets: false         → Home Screen widgets
└── enableWatchApp: false        → watchOS companion

Toggle in AppConfig.swift to enable/disable features
```

## 📱 Screen Sizes Support

```
iPhone SE (Small)
├── Compact width
└── Compact height

iPhone Pro (Medium)
├── Compact width
└── Regular height

iPhone Pro Max (Large)
├── Compact width
└── Regular height

iPad (Extra Large)
├── Regular width
└── Regular height
```

All views use flexible layouts:
- LazyVGrid with .flexible() columns
- HStack/VStack with Spacer()
- frame(maxWidth: .infinity)

## 🎨 Color System

```
Semantic Colors
├── Profit/Gain → .green
├── Loss → .red
├── Neutral → .gray
├── Accent → .blue
└── Status
    ├── Active → .green
    ├── Pending → .orange
    └── Stopped → .red

Adaptive Colors (Light/Dark)
├── .primary → Text
├── .secondary → Subtle text
├── Color(.systemBackground) → Background
├── Color(.systemGray6) → Cards
└── Color(.systemGray5) → Buttons
```

## 🔔 Future Extensibility

### Easy to Add
```
┌─────────────────────────┐
│ Push Notifications      │ → Add AppDelegate + UNUserNotificationCenter
│ Widgets                 │ → Add WidgetKit extension
│ Watch App               │ → Add watchOS target
│ Keychain Security       │ → Use provided KeychainManager
│ Biometric Auth          │ → Add LocalAuthentication
│ Background Refresh      │ → Add background capability
│ Custom Indicators       │ → Extend Chart components
│ Social Features         │ → Add new views
└─────────────────────────┘
```

## 📊 Performance Optimization

```
Implemented:
✅ Lazy loading (LazyVStack, LazyHStack)
✅ Async operations (async/await)
✅ Minimal re-renders (@State optimization)
✅ Native components (no heavy dependencies)

Easy to Add:
□ Image caching
□ Data persistence (SwiftData/CoreData)
□ Pagination
□ Request debouncing
□ Background data sync
```

## 🎯 Testing Points

```
Unit Tests
├── APIService.request()
├── ThemeManager.currentTheme
├── Data model calculations
└── Formatter functions

Integration Tests
├── API → View data flow
├── Navigation paths
└── State management

UI Tests
├── Tab navigation
├── Search functionality
├── Order placement flow
└── Settings changes
```

---

## 📝 Quick Reference

### Adding a New Feature

1. **New View**
   ```swift
   // Create in Views/
   struct NewFeatureView: View {
       var body: some View {
           Text("New Feature")
       }
   }
   ```

2. **Add to Navigation**
   ```swift
   // In MoreView.swift
   NavigationLink(destination: NewFeatureView()) {
       MenuRow(...)
   }
   ```

3. **Add API Endpoint** (if needed)
   ```swift
   // In APIService.swift
   func fetchNewData() async throws -> NewData {
       try await request(endpoint: "/new-endpoint")
   }
   ```

4. **Add Configuration** (if needed)
   ```swift
   // In AppConfig.swift
   enum NewFeature {
       static let setting = true
   }
   ```

---

**This visual guide shows how all pieces fit together! 🎉**

Use this as a reference when:
- Adding new features
- Understanding data flow
- Debugging navigation issues
- Planning architecture changes
