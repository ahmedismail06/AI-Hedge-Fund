# Info.plist Configuration for HTTP API

## ⚠️ CRITICAL: App Transport Security Setup

Your API uses HTTP (not HTTPS): `http://YOUR_BACKEND_HOST:8000`

iOS blocks HTTP connections by default. You MUST add this to your Info.plist:

## Method 1: Using Xcode GUI

1. Open your project in Xcode
2. Select your target
3. Go to "Info" tab
4. Right-click in the list and select "Add Row"
5. Add these entries:

```
App Transport Security Settings (Dictionary)
└── Exception Domains (Dictionary)
    └── YOUR_BACKEND_HOST (Dictionary)
        ├── NSExceptionAllowsInsecureHTTPLoads (Boolean) = YES
        └── NSIncludesSubdomains (Boolean) = YES
```

## Method 2: Editing Info.plist XML Directly

If you open Info.plist as source code, add this inside the `<dict>` tag:

```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSExceptionDomains</key>
    <dict>
        <key>YOUR_BACKEND_HOST</key>
        <dict>
            <key>NSExceptionAllowsInsecureHTTPLoads</key>
            <true/>
            <key>NSIncludesSubdomains</key>
            <true/>
        </dict>
    </dict>
</dict>
```

## Complete Info.plist Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Your existing keys -->
    <key>CFBundleDevelopmentRegion</key>
    <string>$(DEVELOPMENT_LANGUAGE)</string>
    
    <!-- ADD THIS FOR HTTP API -->
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSExceptionDomains</key>
        <dict>
            <key>YOUR_BACKEND_HOST</key>
            <dict>
                <key>NSExceptionAllowsInsecureHTTPLoads</key>
                <true/>
                <key>NSIncludesSubdomains</key>
                <true/>
            </dict>
        </dict>
    </dict>
    
    <!-- Rest of your Info.plist -->
</dict>
</plist>
```

## Why This is Needed

- iOS requires HTTPS for all network connections (App Transport Security)
- Your backend uses HTTP on IP address `YOUR_BACKEND_HOST:8000`
- This configuration creates an exception for that specific IP
- Without this, ALL API calls will fail with ATS errors

## Production Warning

⚠️ **For production apps submitted to the App Store:**
- Apple may reject apps using HTTP
- You should migrate to HTTPS
- Get an SSL certificate for your domain
- Update the baseURL to use `https://`

## Testing the Connection

After adding this to Info.plist:

```swift
// Test in any view
Task {
    do {
        let portfolio = try await APIService.shared.fetchPortfolio()
        print("✅ Connected successfully!")
    } catch {
        print("❌ Connection failed: \(error)")
    }
}
```

## Common Errors Without This Setup

```
Error: The resource could not be loaded because the App Transport Security policy requires the use of a secure connection.
```

If you see this error, you forgot to add the Info.plist configuration!

---

**Remember**: Add this configuration BEFORE making any API calls!
