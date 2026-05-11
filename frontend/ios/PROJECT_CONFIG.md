# Project Configuration Guide

## 📋 Info.plist Settings

Add these keys to your `Info.plist` file if needed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- App Display Name -->
    <key>CFBundleDisplayName</key>
    <string>Trading Dashboard</string>
    
    <!-- App Version -->
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    
    <!-- Build Number -->
    <key>CFBundleVersion</key>
    <string>1</string>
    
    <!-- Allow HTTP if your API doesn't use HTTPS (not recommended for production) -->
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <false/>
        <!-- Or allow specific domains -->
        <key>NSExceptionDomains</key>
        <dict>
            <key>yourdomain.com</key>
            <dict>
                <key>NSExceptionAllowsInsecureHTTPLoads</key>
                <true/>
                <key>NSIncludesSubdomains</key>
                <true/>
            </dict>
        </dict>
    </dict>
    
    <!-- Camera access (if you add QR code scanning for adding stocks) -->
    <key>NSCameraUsageDescription</key>
    <string>We need camera access to scan stock symbols via QR codes</string>
    
    <!-- Face ID / Touch ID access -->
    <key>NSFaceIDUsageDescription</key>
    <string>Use Face ID to securely access your trading dashboard</string>
    
    <!-- Push Notifications -->
    <key>UIBackgroundModes</key>
    <array>
        <string>remote-notification</string>
    </array>
    
    <!-- Supported Interface Orientations -->
    <key>UISupportedInterfaceOrientations</key>
    <array>
        <string>UIInterfaceOrientationPortrait</string>
        <string>UIInterfaceOrientationLandscapeLeft</string>
        <string>UIInterfaceOrientationLandscapeRight</string>
    </array>
</dict>
</plist>
```

## 🎯 Project Build Settings

### Deployment Target
- **Minimum iOS Version**: 17.0 (for latest SwiftUI features)
- **Swift Version**: 5.9 or later

### Capabilities to Enable (if needed)

1. **Push Notifications**
   - Go to Signing & Capabilities tab
   - Click "+ Capability"
   - Add "Push Notifications"

2. **Background Modes** (for live data updates)
   - Add "Background Modes"
   - Check "Remote notifications"
   - Check "Background fetch"

3. **App Groups** (if you add widgets)
   - Add "App Groups"
   - Create group: `group.com.yourcompany.tradingdashboard`

4. **Keychain Sharing** (for secure token storage)
   - Add "Keychain Sharing"
   - Use default keychain group

## 📱 App Icon Setup

Create an App Icon Set in Assets.xcassets:

1. Right-click Assets.xcassets → New App Icon
2. Add icon images for these sizes:
   - 20x20 @2x, @3x
   - 29x29 @2x, @3x
   - 40x40 @2x, @3x
   - 60x60 @2x, @3x
   - 1024x1024 (App Store)

**Quick tip**: Use a tool like [AppIconMaker](https://appiconmaker.co) to generate all sizes from one image.

## 🎨 Launch Screen

Create a simple launch screen in LaunchScreen.storyboard:

1. Open LaunchScreen.storyboard
2. Add your app logo centered
3. Add app name label
4. Keep it simple - it shows briefly

Or use SwiftUI (iOS 14+):
```swift
// In TradingDashboardApp.swift
var body: some Scene {
    WindowGroup {
        ContentView()
            .environmentObject(themeManager)
            .onAppear {
                // Simulate loading
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    // App is ready
                }
            }
    }
}
```

## 🔐 Security Configuration

### Keychain for Tokens

Add a Keychain helper (optional but recommended):

```swift
// Create: Utilities/KeychainManager.swift
import Security
import Foundation

class KeychainManager {
    static let shared = KeychainManager()
    
    func save(key: String, value: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecValueData as String: data
        ]
        
        SecItemDelete(query as CFDictionary)
        let status = SecItemAdd(query as CFDictionary, nil)
        return status == errSecSuccess
    }
    
    func get(key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true
        ]
        
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8)
        else { return nil }
        
        return value
    }
    
    func delete(key: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key
        ]
        
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess
    }
}
```

## 📊 Analytics Configuration (Optional)

If you want to add analytics:

### Firebase
1. Add Firebase via Swift Package Manager
2. Download GoogleService-Info.plist from Firebase Console
3. Add to your project
4. Initialize in App file

### App Store Connect
- Built-in analytics available after submission
- No code needed

## 🧪 Testing Configuration

### Unit Tests Target
Create test files structure:
```
TradingDashboardTests/
├── Services/
│   └── APIServiceTests.swift
├── Managers/
│   └── ThemeManagerTests.swift
└── ViewModels/
    └── DashboardViewModelTests.swift
```

### UI Tests Target
Test critical user flows:
- Login
- View portfolio
- Place order
- Search stocks

## 🚀 App Store Preparation

### Required Assets
1. **App Screenshots** (all device sizes)
   - iPhone 6.7" (Pro Max)
   - iPhone 6.5" (Plus)
   - iPhone 5.5"
   - iPad Pro 12.9"
   - iPad Pro 11"

2. **App Preview Videos** (optional but recommended)
   - 30 seconds max
   - Show key features

3. **App Description**
   - Title: "Trading Dashboard - Portfolio Manager"
   - Subtitle: "Track investments & execute trades"
   - Description: Write compelling copy
   - Keywords: trading, stocks, portfolio, investing

4. **Privacy Policy**
   - Required for App Store
   - Host on your website

### App Store Connect Metadata
```
Primary Category: Finance
Secondary Category: Business
Age Rating: 4+ (unless you have restricted content)
Price: Free (or set price)
In-App Purchases: List if applicable
Subscription: Configure if applicable
```

## 🔔 Push Notifications Setup

### 1. Apple Developer Portal
1. Go to Certificates, Identifiers & Profiles
2. Create APNs Auth Key
3. Download the .p8 file
4. Note the Key ID

### 2. In Xcode
1. Enable Push Notifications capability
2. Add Background Modes → Remote notifications

### 3. In Code
```swift
// In TradingDashboardApp.swift
import UserNotifications

@main
struct TradingDashboardApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    
    // ... rest of code
}

class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey : Any]? = nil
    ) -> Bool {
        requestNotificationPermission()
        return true
    }
    
    func requestNotificationPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if granted {
                DispatchQueue.main.async {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
        }
    }
    
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
        print("Device Token: \(token)")
        // Send token to your server
    }
}
```

## 🎯 Performance Optimization

### Build Settings
```
Optimization Level (Release): -O
Swift Compilation Mode: Whole Module
Enable Bitcode: No (deprecated)
Strip Debug Symbols: Yes (Release only)
```

### Code Optimization Tips
1. Use `@MainActor` for View-related code
2. Use `Task` for async operations
3. Cache images with `AsyncImage` or custom cache
4. Lazy load views with `LazyVStack`/`LazyHStack`
5. Debounce search queries

## 📦 Dependencies Management

Using Swift Package Manager (recommended):

### Add Packages
1. File → Add Package Dependencies
2. Enter package URL
3. Select version
4. Add to target

### Recommended Packages
```
// None required for basic functionality
// Optional enhancements:
- Kingfisher (Image loading)
- Alamofire (Alternative networking)
- SwiftLint (Code quality)
```

## 🔍 Debugging Tools

### Xcode Instruments
- **Time Profiler**: Find slow code
- **Allocations**: Memory leaks
- **Network**: API call monitoring
- **Energy**: Battery usage

### Console Logs
```swift
// Use structured logging
import os

let logger = Logger(subsystem: "com.yourcompany.tradingdashboard", category: "network")

logger.info("Fetching portfolio data")
logger.error("Failed to load: \(error.localizedDescription)")
```

## ✅ Pre-Launch Checklist

- [ ] All features work with sample data
- [ ] App builds without warnings
- [ ] Tested on multiple device sizes
- [ ] Light and dark mode both work
- [ ] No memory leaks
- [ ] API integration complete
- [ ] Error handling in place
- [ ] Loading states implemented
- [ ] App icon set
- [ ] Launch screen created
- [ ] Privacy policy created
- [ ] TestFlight beta tested
- [ ] App Store screenshots ready
- [ ] App description written

---

**You're now configured for success! 🎉**
