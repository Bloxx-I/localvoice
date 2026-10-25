import AppKit
import Foundation
let root = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("LocalVoiceCombo")
let task = Process()
task.executableURL = root.appendingPathComponent(".venv/bin/python")
task.arguments = ["-u", root.appendingPathComponent("src/combined.py").path]
task.currentDirectoryURL = root
var environment = ProcessInfo.processInfo.environment
environment["PYTHONUNBUFFERED"] = "1"
environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
environment["HF_HUB_OFFLINE"] = "1"
environment["TRANSFORMERS_OFFLINE"] = "1"
task.environment = environment
let logs = root.appendingPathComponent("Logs")
try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
let logURL = logs.appendingPathComponent("app.log")
FileManager.default.createFile(atPath: logURL.path, contents: nil)
if let log = try? FileHandle(forWritingTo: logURL) { task.standardOutput = log; task.standardError = log }
do {
    try task.run()
    task.waitUntilExit()
    if task.terminationStatus != 0 {
        let alert = NSAlert()
        alert.messageText = "LocalVoice + reSpeaker konnte nicht gestartet werden"
        alert.informativeText = "Details: " + logURL.path
        alert.runModal()
    }
} catch {
    let alert = NSAlert()
    alert.messageText = "LocalVoice + reSpeaker konnte nicht gestartet werden"
    alert.informativeText = error.localizedDescription
    alert.runModal()
}
