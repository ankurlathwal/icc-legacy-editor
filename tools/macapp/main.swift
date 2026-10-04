// ICC Editor.app — a native macOS window (WebKit, the engine Safari uses) around the editor.
//
// Starts `/usr/bin/python3 -m icc.editor --embedded` from Contents/Resources/runtime, waits for the
// "ICC-EDITOR-URL <url>" line it prints, and shows that page. Closing the window or ⌘Q quits the
// app (after a warning if there are unsaved changes); the editor process stops with it because it
// quits when its stdin closes. Built by tools/build_macos.py.
import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    let serverInput = Pipe()
    var quitting = false
    var confirmedQuit = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "pickFolder")
        config.userContentController.add(self, name: "quit")
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "ICC Editor"
        window.contentView = web
        window.delegate = self
        window.minSize = NSSize(width: 900, height: 600)
        if !window.setFrameUsingName("ICCEditorMain") { window.center() }
        window.setFrameAutosaveName("ICCEditorMain")
        web.loadHTMLString("<html><body style='font:15px -apple-system;color:#888;display:flex;align-items:center;justify-content:center;height:90vh'>Starting the ICC editor…</body></html>", baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startServer()
    }

    // MARK: editor process
    func logURL() -> URL {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("ICC Editor.log")
    }

    func startServer() {
        guard let runtime = Bundle.main.resourceURL?.appendingPathComponent("runtime") else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-m", "icc.editor", "--embedded"]
        p.currentDirectoryURL = runtime
        let out = Pipe()
        p.standardOutput = out
        p.standardInput = serverInput
        FileManager.default.createFile(atPath: logURL().path, contents: nil)
        p.standardError = try? FileHandle(forWritingTo: logURL())
        var buffer = Data()
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            buffer.append(data)
            guard let text = String(data: buffer, encoding: .utf8),
                  let marker = text.range(of: "ICC-EDITOR-URL "),
                  let end = text[marker.upperBound...].firstIndex(of: "\n"),
                  let url = URL(string: String(text[marker.upperBound..<end])) else { return }
            handle.readabilityHandler = { h in _ = h.availableData }   // keep draining
            DispatchQueue.main.async { self?.web.load(URLRequest(url: url)) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                let log = (try? String(contentsOf: self.logURL(), encoding: .utf8)) ?? ""
                let alert = NSAlert()
                alert.alertStyle = .critical
                alert.messageText = "The ICC editor stopped unexpectedly."
                alert.informativeText = String(log.suffix(1500)) + "\n\n(Full log: ~/Library/Logs/ICC Editor.log)"
                alert.runModal()
                self.confirmedQuit = true
                NSApp.terminate(nil)
            }
        }
        do { try p.run(); server = p } catch {
            let alert = NSAlert()
            alert.messageText = "Could not start the ICC editor."
            alert.informativeText = "It needs python3 from Apple's Command Line Tools (run: xcode-select --install).\n\n\(error.localizedDescription)"
            alert.runModal()
            confirmedQuit = true
            NSApp.terminate(nil)
        }
    }

    // MARK: quitting
    func hasUnsavedChanges(_ done: @escaping (Bool) -> Void) {
        let js = "(function(){var b=document.querySelector('#save');" +
                 "return !!((b && !b.disabled && !b.hidden) || (typeof pending !== 'undefined' && pending));})()"
        web.evaluateJavaScript(js) { result, _ in done((result as? Bool) ?? false) }
    }

    func confirmQuit(_ done: @escaping (Bool) -> Void) {
        hasUnsavedChanges { dirty in
            guard dirty else { return done(true) }
            let alert = NSAlert()
            alert.messageText = "Quit without saving?"
            alert.informativeText = "You have changes that are not saved to the game files."
            alert.addButton(withTitle: "Cancel")
            alert.addButton(withTitle: "Quit Anyway")
            alert.beginSheetModal(for: self.window) { done($0 == .alertSecondButtonReturn) }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if confirmedQuit { return .terminateNow }
        confirmQuit { ok in NSApp.reply(toApplicationShouldTerminate: ok) }   // asks only if there are unsaved changes
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        try? serverInput.fileHandleForWriting.close()   // the editor quits when its stdin closes
        server?.terminate()
    }

    // MARK: page dialogs (window.alert / confirm) and the native folder panel
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "quit" {   // the page's Quit button has already asked about unsaved changes
            confirmedQuit = true
            NSApp.terminate(nil)
            return
        }
        guard message.name == "pickFolder" else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Open"
        panel.message = "Choose the ICC game folder to edit (ICC 2, 2000, 2002 or 2006)"
        if let start = message.body as? String, !start.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: start)
        }
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url,
                  let json = try? JSONSerialization.data(withJSONObject: [url.path]),
                  let arg = String(data: json, encoding: .utf8) else { return }
            self.web.evaluateJavaScript("window.iccFolderPicked(\(arg.dropFirst().dropLast()))")
        }
    }

    // MARK: menus (also gives text fields the usual ⌘C / ⌘V / ⌘Z shortcuts)
    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let item = NSMenuItem()
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            item.submenu = menu
            main.addItem(item)
        }
        func item(_ title: String, _ action: Selector?, _ key: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
            i.keyEquivalentModifierMask = mods
            return i
        }
        add("ICC Editor", [item("About ICC Editor", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
                           .separator(),
                           item("Hide ICC Editor", #selector(NSApplication.hide(_:)), "h"),
                           item("Quit ICC Editor", #selector(NSApplication.terminate(_:)), "q")])
        add("Edit", [item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "z", [.command, .shift]),
                     .separator(),
                     item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
                     item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a")])
        add("View", [item("Reload", #selector(WKWebView.reload(_:)), "r"),
                     item("Actual Size", #selector(resetZoom), "0"),
                     item("Zoom In", #selector(zoomIn), "+"), item("Zoom Out", #selector(zoomOut), "-")])
        add("Window", [item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
                       item("Close", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = main
    }

    @objc func zoomIn() { web.pageZoom = min(web.pageZoom + 0.1, 2.0) }
    @objc func zoomOut() { web.pageZoom = max(web.pageZoom - 0.1, 0.6) }
    @objc func resetZoom() { web.pageZoom = 1.0 }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
