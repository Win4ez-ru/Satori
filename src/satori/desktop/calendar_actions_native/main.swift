// Create-only READY/COMMIT helper. --offline-probe never constructs EKEventStore.
// The existing shift-import helper and its authority remain unchanged.
import CryptoKit
import Darwin
import EventKit
import Foundation

private let maxFrame = 32 * 1024
private let markerPrefix = "satori-operation://calendar-create/v1/"
private let zero = "00000000-0000-0000-0000-000000000000"

private enum SafeError: String, Error {
    case invalid = "calendar_action_provider_failed"
    case permission = "calendar_action_permission_required"
    case notWritable = "calendar_action_not_writable"
    case scope = "calendar_action_scope_mismatch"
    case stale = "calendar_action_stale"
    case unavailable = "calendar_action_provider_unavailable"
    case unknown = "calendar_action_unknown_no_replay"
}

private struct Envelope: Decodable {
    let schema_version: Int
    let operation: String
    let attempt_id: String
    let idempotency_token: String
    let payload_digest: String
    let payload_json_base64: String
    let claimed_at: Double
    let expires_at: Double
    let read_deadline: Double
}

private struct Commit: Decodable {
    let schema_version: Int
    let operation: String
    let attempt_id: String
    let idempotency_token: String
    let payload_digest: String
    let nonce: String
}

private struct Command: Decodable {
    let request_id: String
    let context_digest: String
    let title: String
    let starts_at: String
    let ends_at: String
    let timezone: String
}

private struct Resource: Decodable {
    let identity_id: String
    let profile_id: String
    let provider: String
    let account_id: String
    let calendar_id: String
    let timezone: String
}

private struct Material: Decodable {
    let schema: String
    let command: Command
    let resource: Resource
}

private struct Parsed {
    let envelope: Envelope
    let material: Material
    let start: Date
    let end: Date
    let zone: TimeZone
}

private struct Reply: Encodable {
    let schema_version = 1
    let operation: String
    let attempt_id: String
    let idempotency_token: String
    let payload_digest: String
    let status: String
    let observed_at: String
    let nonce: String?
    let event_id: String?
    let exact_fields_verified: Bool
    let fresh_store_verified: Bool
    let remote_sync_verified = false
    let reason: String?
}

private func utc(_ date: Date) -> String {
    let f = ISO8601DateFormatter()
    f.timeZone = TimeZone(secondsFromGMT: 0)
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f.string(from: date)
}

private func hash(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func lowerHash(_ value: String) -> Bool {
    value.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) }
}

private func identifier(_ value: String) -> Bool {
    !value.isEmpty && value.unicodeScalars.count <= 512
        && value == value.trimmingCharacters(in: .whitespacesAndNewlines)
        && !value.contains("*")
        && !value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
}

private func nonzeroUUID(_ value: String) -> Bool {
    value != zero && UUID(uuidString: value)?.uuidString.lowercased() == value
}

private func exactKeys(_ object: Any, _ keys: Set<String>) throws {
    guard let value = object as? [String: Any], Set(value.keys) == keys else {
        throw SafeError.invalid
    }
}

private func line() throws -> Data? {
    var result = Data()
    var byte: UInt8 = 0
    while true {
        let count = Darwin.read(STDIN_FILENO, &byte, 1)
        if count == 0 {
            guard result.isEmpty else { throw SafeError.invalid }
            return nil
        }
        guard count == 1 else { throw SafeError.invalid }
        if byte == 10 { return result }
        result.append(byte)
        guard result.count < maxFrame else { throw SafeError.invalid }
    }
}

private func parsed(_ data: Data) throws -> Parsed {
    let object = try JSONSerialization.jsonObject(with: data)
    try exactKeys(object, ["schema_version", "operation", "attempt_id", "idempotency_token",
        "payload_digest", "payload_json_base64", "claimed_at", "expires_at", "read_deadline"])
    let e = try JSONDecoder().decode(Envelope.self, from: data)
    guard e.schema_version == 1, ["prepare", "lookup"].contains(e.operation),
          nonzeroUUID(e.attempt_id), lowerHash(e.idempotency_token), lowerHash(e.payload_digest),
          e.claimed_at.isFinite, e.expires_at.isFinite, e.read_deadline.isFinite,
          e.expires_at > e.claimed_at, e.expires_at - e.claimed_at <= 30.000001,
          let body = Data(base64Encoded: e.payload_json_base64), body.count <= 16384,
          hash(body) == e.payload_digest else { throw SafeError.invalid }
    let materialObject = try JSONSerialization.jsonObject(with: body)
    try exactKeys(materialObject, ["schema", "command", "resource"])
    guard let dictionary = materialObject as? [String: Any],
          let commandObject = dictionary["command"], let resourceObject = dictionary["resource"]
    else { throw SafeError.invalid }
    try exactKeys(commandObject, ["request_id", "context_digest", "title", "starts_at", "ends_at", "timezone"])
    try exactKeys(resourceObject, ["identity_id", "profile_id", "provider", "account_id", "calendar_id", "timezone"])
    let material = try JSONDecoder().decode(Material.self, from: body)
    let c = material.command, r = material.resource
    let invalidCategory: Set<Unicode.GeneralCategory> = [.control, .surrogate, .format]
    guard material.schema == "satori.calendar-create.v1", r.provider == "macos_eventkit",
          nonzeroUUID(c.request_id), nonzeroUUID(r.identity_id), nonzeroUUID(r.profile_id),
          lowerHash(c.context_digest), identifier(r.account_id), identifier(r.calendar_id),
          !c.title.isEmpty, c.title.unicodeScalars.count <= 500,
          c.title == c.title.trimmingCharacters(in: .whitespacesAndNewlines),
          c.title == c.title.precomposedStringWithCanonicalMapping,
          !c.title.unicodeScalars.contains(where: { invalidCategory.contains($0.properties.generalCategory) }),
          c.timezone == r.timezone, let zone = TimeZone(identifier: c.timezone)
    else { throw SafeError.invalid }
    let f = ISO8601DateFormatter()
    f.timeZone = TimeZone(secondsFromGMT: 0)
    f.formatOptions = [.withInternetDateTime]
    guard c.starts_at.hasSuffix("Z"), c.ends_at.hasSuffix("Z"),
          let start = f.date(from: c.starts_at), let end = f.date(from: c.ends_at),
          f.string(from: start) == c.starts_at, f.string(from: end) == c.ends_at,
          end > start, end.timeIntervalSince(start) <= 7 * 86400 else { throw SafeError.invalid }
    let tokenMaterial: [String: String] = ["schema": "satori.calendar-create-attempt.v1",
        "request_id": c.request_id, "payload_digest": e.payload_digest]
    let tokenBytes = try JSONSerialization.data(withJSONObject: tokenMaterial,
                                               options: [.sortedKeys, .withoutEscapingSlashes])
    guard hash(tokenBytes) == e.idempotency_token else { throw SafeError.invalid }
    return Parsed(envelope: e, material: material, start: start, end: end, zone: zone)
}

private func reply(_ p: Parsed, operation: String, status: String, nonce: String? = nil,
                   eventID: String? = nil, verified: Bool = false, fresh: Bool = false,
                   reason: SafeError? = nil) throws {
    let e = p.envelope
    let value = Reply(operation: operation, attempt_id: e.attempt_id,
        idempotency_token: e.idempotency_token, payload_digest: e.payload_digest,
        status: status, observed_at: utc(Date()), nonce: nonce, event_id: eventID,
        exact_fields_verified: verified, fresh_store_verified: fresh, reason: reason?.rawValue)
    let data = try JSONEncoder().encode(value)
    guard data.count < maxFrame else { throw SafeError.invalid }
    FileHandle.standardOutput.write(data + Data([10]))
}

@available(macOS 14.0, *)
private func fullAccess() throws {
    guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else {
        throw SafeError.permission
    }
}

@available(macOS 14.0, *)
private func target(_ p: Parsed, _ store: EKEventStore, write: Bool) throws -> EKCalendar {
    try fullAccess()
    let r = p.material.resource
    guard let calendar = store.calendar(withIdentifier: r.calendar_id),
          calendar.source?.sourceIdentifier == r.account_id else { throw SafeError.scope }
    guard calendar.allowedEntityTypes.contains(.event) else { throw SafeError.notWritable }
    if write && (!calendar.allowsContentModifications || calendar.type == .subscription
                 || calendar.type == .birthday) { throw SafeError.notWritable }
    return calendar
}

@available(macOS 14.0, *)
private func boundedEvents(_ p: Parsed, _ store: EKEventStore, margin: Double) throws -> [EKEvent] {
    let calendar = try target(p, store, write: false)
    let predicate = store.predicateForEvents(withStart: p.start.addingTimeInterval(-margin),
        end: p.end.addingTimeInterval(margin), calendars: [calendar])
    var result: [EKEvent] = []
    var failure: SafeError? = nil
    store.enumerateEvents(matching: predicate) { event, stop in
        if event.calendar?.calendarIdentifier != p.material.resource.calendar_id
            || event.calendar?.source?.sourceIdentifier != p.material.resource.account_id {
            failure = .scope; stop.pointee = true; return
        }
        if event.status == .canceled { return }
        result.append(event)
        if result.count > 1000 { failure = .stale; stop.pointee = true }
    }
    if let failure { throw failure }
    try fullAccess()
    return result
}

private func exact(_ event: EKEvent, _ p: Parsed) -> Bool {
    event.title == p.material.command.title && event.startDate == p.start && event.endDate == p.end
        && event.timeZone?.identifier == p.zone.identifier && !event.isAllDay
        && event.url?.absoluteString == markerPrefix + p.envelope.idempotency_token
        && !event.hasRecurrenceRules && !(event.hasAttendees)
        && (event.alarms ?? []).isEmpty && (event.notes ?? "").isEmpty
        && (event.location ?? "").isEmpty && event.structuredLocation == nil
        && event.calendar?.calendarIdentifier == p.material.resource.calendar_id
        && event.calendar?.source?.sourceIdentifier == p.material.resource.account_id
}

@available(macOS 14.0, *)
private func preflight(_ p: Parsed, _ store: EKEventStore) throws -> EKCalendar {
    guard Date().timeIntervalSince1970 < p.envelope.expires_at, p.start > Date(),
          p.start.timeIntervalSinceNow <= 1830 * 86400 else { throw SafeError.stale }
    let calendar = try target(p, store, write: true)
    let events = try boundedEvents(p, store, margin: 86400)
    for event in events {
        if event.url?.absoluteString == markerPrefix + p.envelope.idempotency_token {
            throw SafeError.stale
        }
        guard let start = event.startDate, let end = event.endDate, end > start else {
            throw SafeError.stale
        }
        if start < p.end && end > p.start { throw SafeError.stale }
    }
    return calendar
}

@available(macOS 14.0, *)
private func lookup(_ p: Parsed, operation: String, created: Bool) throws {
    guard Date().timeIntervalSince1970 < (created ? p.envelope.expires_at : p.envelope.read_deadline)
    else { throw SafeError.unknown }
    try fullAccess()
    let fresh = EKEventStore()
    let events = try boundedEvents(p, fresh, margin: 86400)
    let owned = events.filter { $0.url?.absoluteString == markerPrefix + p.envelope.idempotency_token }
    if owned.count == 1, let event = owned.first, exact(event, p),
       let eventID = event.eventIdentifier, identifier(eventID) {
        try reply(p, operation: operation, status: created ? "created" : "owned_exact",
                  eventID: eventID, verified: true, fresh: true)
    } else if created {
        throw SafeError.unknown
    } else {
        try reply(p, operation: operation, status: owned.isEmpty ? "absent" : "conflict", fresh: true)
    }
}

@available(macOS 14.0, *)
private func run(_ p: Parsed) throws {
    if p.envelope.operation == "lookup" {
        guard try line() == nil, p.envelope.read_deadline > Date().timeIntervalSince1970,
              p.envelope.read_deadline - Date().timeIntervalSince1970 <= 30.5 else { throw SafeError.invalid }
        do { try lookup(p, operation: "lookup", created: false) }
        catch { try reply(p, operation: "lookup", status: "unknown") }
        return
    }
    do {
        try fullAccess()
        let store = EKEventStore()
        _ = try preflight(p, store)
        let nonce = UUID().uuidString.lowercased()
        try reply(p, operation: "prepare", status: "ready", nonce: nonce)
        guard let commitBytes = try line() else { return }
        let object = try JSONSerialization.jsonObject(with: commitBytes)
        try exactKeys(object, ["schema_version", "operation", "attempt_id", "idempotency_token", "payload_digest", "nonce"])
        let c = try JSONDecoder().decode(Commit.self, from: commitBytes)
        guard c.schema_version == 1, c.operation == "commit", c.attempt_id == p.envelope.attempt_id,
              c.idempotency_token == p.envelope.idempotency_token,
              c.payload_digest == p.envelope.payload_digest, c.nonce == nonce,
              try line() == nil else { throw SafeError.invalid }
        var mayHaveSaved = false
        do {
            try fullAccess()
            // Initial READY observations must not hide another client's intervening event.
            let commitStore = EKEventStore()
            let calendar = try preflight(p, commitStore)
            let event = EKEvent(eventStore: commitStore)
            event.calendar = calendar; event.title = p.material.command.title
            event.startDate = p.start; event.endDate = p.end; event.timeZone = p.zone
            event.isAllDay = false
            event.url = URL(string: markerPrefix + p.envelope.idempotency_token)
            event.alarms = []; event.recurrenceRules = []
            // No invitation/guest, note, attachment, location, update, delete or calendar-create API.
            // Another Calendar client can race this final precondition check; no global atomicity claim.
            mayHaveSaved = true
            try commitStore.save(event, span: .thisEvent, commit: true)
            try lookup(p, operation: "commit", created: true)
        } catch let error as SafeError where !mayHaveSaved && error != .unknown {
            try reply(p, operation: "commit", status: "rejected", reason: error)
        } catch {
            try reply(p, operation: "commit", status: "unknown", reason: .unknown)
        }
    } catch let error as SafeError {
        try reply(p, operation: "prepare", status: "rejected", reason: error)
    } catch {
        try reply(p, operation: "prepare", status: "rejected", reason: .invalid)
    }
}

private func main() -> Int32 {
    do {
        guard CommandLine.arguments.count == 2,
              ["--offline-probe", "--calendar-actions-v1"].contains(CommandLine.arguments[1]),
              let data = try line() else { return 2 }
        let p = try parsed(data)
        if CommandLine.arguments[1] == "--offline-probe" {
            guard try line() == nil else { return 2 }
            // Parsing/hash/strict field checks only: no accessStatus or EKEventStore on this branch.
            let response: [String: Any] = ["schema_version": 1, "status": "valid",
                "attempt_id": p.envelope.attempt_id, "payload_digest": p.envelope.payload_digest,
                "eventkit_opened": false]
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: response))
            return 0
        }
        alarm(32)
        if #available(macOS 14.0, *) { try run(p) }
        else { try reply(p, operation: p.envelope.operation, status: "rejected", reason: .unavailable) }
        return 0
    } catch {
        // Never print native errors, request material, titles or identifiers on failure.
        FileHandle.standardOutput.write(Data("{\"status\":\"invalid\"}\n".utf8))
        return 2
    }
}

exit(main())
