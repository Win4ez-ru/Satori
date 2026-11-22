// One explicitly requested native notification operation; no source or model access.
import AppKit
import Foundation
import UserNotifications

struct Request: Decodable, Sendable {
    let operation: String
    let attempt_id: String?
    let reminder_id: String?
    let notification_identifier: String?
    let due_at: String?
    let payload_digest: String?
    let title: String?
}

final class ReplyBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: [String: Any]?
    func set(_ reply: [String: Any]) { lock.lock(); defer { lock.unlock() }; if value == nil { value = reply } }
    func get() -> [String: Any]? { lock.lock(); defer { lock.unlock() }; return value }
}

func date(_ text: String) -> Date? {
    let format = ISO8601DateFormatter()
    format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let value = format.date(from: text) { return value }
    format.formatOptions = [.withInternetDateTime]
    return format.date(from: text)
}

func timestamp() -> String {
    let format = ISO8601DateFormatter()
    format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    format.timeZone = TimeZone(secondsFromGMT: 0)
    return format.string(from: Date())
}

func emit(_ value: [String: Any]) {
    guard let output = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { exit(3) }
    FileHandle.standardOutput.write(output)
    FileHandle.standardOutput.write(Data([10]))
}

func readRequest() -> Request? {
    var data = Data()
    while data.count <= 16_384 {
        guard let chunk = try? FileHandle.standardInput.read(upToCount: min(4096, 16_385 - data.count)), !chunk.isEmpty else { break }
        data.append(chunk)
        if let newline = data.firstIndex(of: 10) {
            guard data[data.index(after: newline)...].allSatisfy({ [9, 10, 13, 32].contains($0) }) else { return nil }
            data = data.prefix(upTo: newline)
            break
        }
    }
    guard data.count <= 16_384,
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          Set(object.keys).isSubset(of: ["schema_version", "owner_id", "generation", "attempt_id", "reminder_id", "operation", "notification_identifier", "due_at", "payload_digest", "title"]),
          let request = try? JSONDecoder().decode(Request.self, from: data),
          ["authorize", "status", "schedule", "cancel", "reconcile"].contains(request.operation) else { return nil }
    if request.operation == "authorize" || request.operation == "status" { return request }
    guard let attempt = request.attempt_id, UUID(uuidString: attempt) != nil,
          let reminder = request.reminder_id, UUID(uuidString: reminder) != nil,
          request.notification_identifier == "satori.reminder.\(reminder)",
          let due = request.due_at, date(due) != nil,
          let digest = request.payload_digest, digest.count == 64,
          digest.allSatisfy({ "0123456789abcdef".contains($0) }) else { return nil }
    if let title = request.title {
        guard !title.isEmpty, title.count <= 500,
              !title.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { return nil }
    }
    return request
}

func run(_ request: Request) -> Int32 {
    let center = UNUserNotificationCenter.current()
    let box = ReplyBox()
    func reply(_ status: String) {
        if request.operation == "status" || request.operation == "authorize" {
            box.set(["operation": request.operation, "status": status, "observed_at": timestamp()])
            return
        }
        box.set([
            "attempt_id": request.attempt_id!, "reminder_id": request.reminder_id!,
            "operation": request.operation, "notification_identifier": request.notification_identifier!,
            "payload_digest": request.payload_digest!, "status": status,
            "observed_at": timestamp(), "platform": "native_os"
        ])
    }
    func matches(_ value: UNNotificationRequest, pending: Bool = false) -> Bool {
        guard value.identifier == request.notification_identifier,
              value.content.userInfo["satoriDigest"] as? String == request.payload_digest,
              let raw = value.content.userInfo["satoriDue"] as? String,
              let expected = request.due_at.flatMap(date), let actual = date(raw),
              abs(expected.timeIntervalSince(actual)) < 0.01 else { return false }
        if pending {
            guard let trigger = value.trigger as? UNCalendarNotificationTrigger,
                  !trigger.repeats, let next = trigger.nextTriggerDate(),
                  abs(next.timeIntervalSince(expected)) < 1.1 else { return false }
        }
        return true
    }
    func inspect(_ cancel: Bool = false) {
        center.getPendingNotificationRequests { pending in
            let exact = pending.filter { $0.identifier == request.notification_identifier }
            if !exact.isEmpty {
                if cancel { reply("unknown") }
                else if exact.count == 1 && matches(exact[0], pending: true) { reply("scheduled") }
                else { reply("unknown") }
                return
            }
            center.getDeliveredNotifications { delivered in
                let exactDelivered = delivered.filter { $0.request.identifier == request.notification_identifier }
                if cancel { reply(exactDelivered.isEmpty ? "cancelled" : "unknown") }
                else if exactDelivered.count == 1 && matches(exactDelivered[0].request) { reply("delivered") }
                else { reply(exactDelivered.isEmpty ? "absent" : "unknown") }
            }
        }
    }
    if request.operation == "authorize" {
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            reply(error == nil ? (granted ? "authorized" : "denied") : "unavailable")
        }
    } else if request.operation == "status" {
        center.getNotificationSettings { settings in
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: reply("authorized")
            case .denied: reply("denied")
            case .notDetermined: reply("not_determined")
            @unknown default: reply("unavailable")
            }
        }
    } else if request.operation == "reconcile" { inspect() }
    else if request.operation == "cancel" {
        center.removePendingNotificationRequests(withIdentifiers: [request.notification_identifier!])
        center.removeDeliveredNotifications(withIdentifiers: [request.notification_identifier!])
        inspect(true)
    } else {
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else {
                reply("unavailable"); return
            }
            guard let due = request.due_at.flatMap(date), due > Date(), due.timeIntervalSinceNow <= 1830 * 86_400 else {
                reply("unknown"); return
            }
            center.getPendingNotificationRequests { pending in
                if let existing = pending.first(where: { $0.identifier == request.notification_identifier }) {
                    reply(matches(existing, pending: true) ? "scheduled" : "unknown"); return
                }
                let content = UNMutableNotificationContent()
                content.title = "Сатори"
                content.body = request.title ?? "Напоминание Сатори"
                content.sound = .default
                content.userInfo = ["satoriDigest": request.payload_digest!, "satoriDue": request.due_at!]
                var calendar = Calendar(identifier: .gregorian)
                calendar.timeZone = TimeZone(secondsFromGMT: 0)!
                let fields: Set<Calendar.Component> = [.year, .month, .day, .hour, .minute, .second]
                var components = calendar.dateComponents(fields, from: due)
                components.timeZone = calendar.timeZone
                let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                guard let next = trigger.nextTriggerDate(), abs(next.timeIntervalSince(due)) < 1.1 else {
                    reply("unknown"); return
                }
                let notification = UNNotificationRequest(identifier: request.notification_identifier!, content: content, trigger: trigger)
                center.add(notification) { error in
                    if error != nil { reply("unknown") } else { inspect() }
                }
            }
        }
    }
    let deadline = Date().addingTimeInterval(request.operation == "authorize" ? 120 : 10)
    while box.get() == nil && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
    if let result = box.get() { emit(result); return 0 }
    reply("unknown")
    if let result = box.get() { emit(result) }
    return 3
}

guard let request = readRequest() else {
    emit(["status": "error", "code": "notification_request_invalid"])
    exit(3)
}
exit(run(request))
