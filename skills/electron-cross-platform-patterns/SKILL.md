---
name: electron-cross-platform-patterns
description: >
  Cross-platform Electron patterns for Windows + macOS. IPC safety,
  URL whitelisting, navigation guards, tray management, platform detection.
  Extracted from A>> (Andre)'s TWR and google-vibe-os projects.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /electron-patterns
  - "Electron security"
  - "Electron cross-platform"
  - "IPC pattern"
see_also:
  - swiss-nihilism-design
  - design-principles
origin:
  team_member: "A>>"
  projects: ["twr", "google-vibe-os"]
  evidence: "TWR PRs #1-#2, google-vibe-os security hardening"
---

# Electron Cross-Platform Patterns

Safety-first patterns for Electron apps targeting Windows and macOS.

---

## 1. IPC Safety (contextBridge)

Never expose Node.js APIs directly to the renderer. Always use contextBridge.

```javascript
// preload.js — the ONLY bridge between main and renderer
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Whitelist specific channels
  send: (channel, data) => {
    const validChannels = ['save-file', 'load-settings', 'update-tray'];
    if (validChannels.includes(channel)) {
      ipcRenderer.send(channel, data);
    }
  },
  receive: (channel, callback) => {
    const validChannels = ['file-saved', 'settings-loaded'];
    if (validChannels.includes(channel)) {
      ipcRenderer.on(channel, (_event, ...args) => callback(...args));
    }
  }
});
```

### Rules
- Whitelist ALL IPC channels explicitly
- Never pass `ipcRenderer` directly to renderer
- Validate data shape in main process handlers
- Use `invoke`/`handle` for request-response (not `send`/`on`)

---

## 2. URL Whitelisting

Prevent navigation to untrusted URLs.

```javascript
// main.js
const ALLOWED_ORIGINS = [
  'file://',           // Local files
  'https://api.example.com',  // Your API
];

win.webContents.on('will-navigate', (event, url) => {
  if (!ALLOWED_ORIGINS.some(origin => url.startsWith(origin))) {
    event.preventDefault();
    log.warn(`Blocked navigation to: ${url}`);
  }
});

// Block new window creation
win.webContents.setWindowOpenHandler(({ url }) => {
  if (url.startsWith('https://')) {
    shell.openExternal(url);  // Open in default browser
  }
  return { action: 'deny' };  // Never open new Electron windows
});
```

---

## 3. Platform Detection

```javascript
const isMac = process.platform === 'darwin';
const isWin = process.platform === 'win32';

// Paths
const appData = isMac
  ? path.join(os.homedir(), 'Library', 'Application Support', appName)
  : path.join(process.env.APPDATA || os.homedir(), appName);

// Keyboard shortcuts
const modifier = isMac ? 'Cmd' : 'Ctrl';

// Tray
const trayIcon = isMac ? 'icon-mac-template.png' : 'icon-win.ico';
```

### Platform-Specific Concerns

| Concern | macOS | Windows |
|---------|-------|---------|
| Tray icon | Template image (16x16) | ICO format (multiple sizes) |
| Quit behaviour | Hide on close, Cmd+Q to quit | Close = quit |
| Shortcuts | Cmd+key | Ctrl+key |
| Paths | ~/Library/Application Support/ | %APPDATA% |
| Code signing | Apple Developer ID | Windows Authenticode |
| Auto-update | DMG/ZIP via Sparkle or electron-updater | NSIS/Squirrel |

---

## 4. Build Configuration

```json
{
  "build": {
    "appId": "com.example.appname",
    "mac": {
      "target": ["dmg", "zip"],
      "hardenedRuntime": true,
      "gatekeeperAssess": false
    },
    "win": {
      "target": ["nsis"],
      "publisherName": "CodeTonight"
    }
  }
}
```

---

## 5. Security Checklist

- [ ] `nodeIntegration: false` (default since Electron 12)
- [ ] `contextIsolation: true` (default since Electron 12)
- [ ] `sandbox: true` on BrowserWindow
- [ ] IPC channels whitelisted in preload
- [ ] Navigation guard on `will-navigate`
- [ ] Window open handler blocks new windows
- [ ] No `shell.openExternal` with user-controlled URLs without validation
- [ ] CSP headers set for renderer
- [ ] No `eval()` or `Function()` in renderer
- [ ] Auto-updater uses HTTPS with certificate pinning
