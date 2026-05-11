# Trading Dashboard - iOS App

A comprehensive iOS trading dashboard app built with SwiftUI, designed for portfolio management, trade execution, research, and market analysis.

## 📱 Features

### Dashboard
- Real-time portfolio value and performance tracking
- Interactive performance charts with multiple timeframes
- Quick stats grid (Win Rate, Positions, Avg Return, Sharpe Ratio)
- Top movers section showing best/worst performers
- Recent activity feed with trade history

### Portfolio
- Complete holdings overview with total value and P&L
- Individual position tracking with detailed metrics
- Sortable by value, performance, or alphabetically
- Searchable position list
- Detailed position view with buy/sell actions

### Execution
- Active orders management
- Order history with fill status
- Trade fills tracking
- Create new orders (Market, Limit, Stop, Stop Limit)
- Swipe actions for quick order management

### Research
- Stock search and analysis
- Watchlist management
- Market indices overview (S&P 500, Dow Jones, NASDAQ)
- Earnings calendar
- Latest research and news
- Detailed stock views with charts, news, and statistics

### Additional Tools (More Tab)

#### Screener
- Filter stocks by price, market cap, sector
- Quick preset filters
- Real-time results with key metrics

#### Macro
- Economic indicators (GDP, Unemployment, Inflation, Fed Funds Rate)
- Market sentiment analysis (VIX, Put/Call Ratio, Market Breadth)
- Global markets tracking

#### Risk Analysis
- Overall portfolio risk score
- Risk metrics (Beta, Sharpe Ratio, Max Drawdown, Volatility, VaR)
- Sector concentration analysis
- Visual risk indicators

#### Orchestrator
- Automated trading strategies management
- Strategy performance tracking
- Combined performance metrics
- Enable/disable strategies with toggle

## 🏗️ Project Structure

```
frontend_swift/
├── TradingDashboardApp.swift       # App entry point
├── ContentView.swift               # Main navigation (TabView)
├── Managers/
│   └── ThemeManager.swift          # Theme and appearance management
└── Views/
    ├── DashboardView.swift         # Main dashboard
    ├── PortfolioView.swift         # Portfolio & positions
    ├── ExecutionView.swift         # Trade execution & orders
    ├── ResearchView.swift          # Stock research & analysis
    └── MoreView.swift              # Additional features hub
```

## 🎨 Design Principles

- **Native iOS Design**: Uses SwiftUI with native components
- **Tab Navigation**: Easy access to main features via tab bar
- **Cards & Lists**: Clean, organized information display
- **Color-Coded Data**: Green for gains, red for losses
- **Responsive**: Adapts to different screen sizes
- **Dark Mode**: Automatic support via system theme

## 🚀 Getting Started

### Prerequisites
- Xcode 15.0+
- iOS 17.0+
- Swift 5.9+

### Installation

1. Create a new Xcode project:
   - Open Xcode
   - Select "Create a new Xcode project"
   - Choose "App" under iOS
   - Name it "TradingDashboard"
   - Select SwiftUI for Interface and Swift for Language

2. Add the files:
   - Copy all Swift files from `frontend_swift/` to your Xcode project
   - Maintain the folder structure (Managers/, Views/)

3. Update your project structure:
   - Delete the default `ContentView.swift` if it conflicts
   - Ensure `TradingDashboardApp.swift` is set as the app entry point

4. Build and run:
   - Select your target device or simulator
   - Press Cmd+R to build and run

## 📊 Data Models

### Position
```swift
struct Position: Identifiable {
    let symbol: String
    let name: String
    let shares: Double
    let avgCost: Double
    let currentPrice: Double
    var currentValue: Double
    var costBasis: Double
    var gainLoss: Double
    var percentChange: Double
}
```

### Order
```swift
struct Order: Identifiable {
    let symbol: String
    let type: String
    let quantity: Double
    let price: Double
    let status: String
}
```

### Stock
```swift
struct Stock: Identifiable {
    let symbol: String
    let name: String
    let price: Double
    let change: Double
    let marketCap: String
}
```

## 🔧 Customization

### Changing Colors
Edit `ThemeManager.swift`:
```swift
@Published var accentColor: Color = .blue  // Change to your preferred color
```

### Adding New Features
1. Create a new view in `Views/` folder
2. Add navigation link in `MoreView.swift` or add to `ContentView.swift` tab bar

### Connecting to Real Data
Replace sample data in views with API calls:
```swift
// Example in DashboardView
private func fetchPortfolioData() async {
    // Your API call here
    let response = await APIClient.getPortfolio()
    // Update state
}
```

## 📱 Tab Navigation Structure

1. **Dashboard** - Overview and charts
2. **Portfolio** - Holdings and positions
3. **Execution** - Orders and trades
4. **Research** - Stocks and analysis
5. **More** - Additional tools and settings

## 🎯 Key Components

### Charts
Uses Swift Charts framework for:
- Line charts (Portfolio performance)
- Area charts (Performance visualization)
- Custom chart marks and styling

### Lists
- Searchable lists for positions
- Swipeable rows for quick actions
- Section headers with controls

### Forms
- Order entry forms
- Settings configuration
- Input validation

## 🔐 Future Enhancements

- [ ] Real-time data integration via WebSocket
- [ ] Push notifications for price alerts
- [ ] Face ID / Touch ID authentication
- [ ] Widget support for Home Screen
- [ ] Apple Watch companion app
- [ ] iPad optimization with split views
- [ ] Export portfolio reports (PDF)
- [ ] Integration with brokerage APIs
- [ ] Advanced charting with technical indicators
- [ ] Social trading features

## 📝 Notes

- All data is currently **sample/mock data** for demonstration
- Colors (green/red) automatically adapt to color blindness settings
- Supports both light and dark mode
- Uses system fonts for accessibility
- VoiceOver compatible

## 🤝 Integration with Web Frontend

This iOS app is designed to complement the React web dashboard:
- Shares similar UI patterns and information architecture
- Can connect to the same backend API
- Provides mobile-optimized experience
- Supports push notifications (web doesn't)

## 📄 License

This is a demo project structure. Add your own license as needed.

---

**Built with ❤️ using SwiftUI**
