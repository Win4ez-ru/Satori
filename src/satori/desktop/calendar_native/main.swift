// One-shot selected-calendar adapter and explicitly reviewed schedule creation.
import CoreFoundation
import CoreGraphics
import CryptoKit
import Darwin
import EventKit
import Foundation
import ImageIO
import Vision

private let maximumInput = 256 * 1024
private let maximumOutput = 512 * 1024
private let zeroRequestID = "00000000-0000-0000-0000-000000000000"

private enum Operation: String, Codable {
    case status, requestAccess = "request_access", listCalendars = "list_calendars"
    case readEvents = "read_events"
    case recognizeScheduleImage = "recognize_schedule_image"
    case preflightSchedule = "preflight_schedule", createSchedule = "create_schedule"
    case reconcileSchedule = "reconcile_schedule"
}

private enum Access: String, Encodable {
    case notDetermined = "not_determined", restricted, denied
    case writeOnly = "write_only", fullAccess = "full_access", unknown
}

private enum SafeError: String, Error, Encodable {
    case invalidRequest = "invalid_request"
    case permissionRequired = "permission_required"
    case permissionDenied = "permission_denied"
    case permissionRestricted = "permission_restricted"
    case permissionRequestFailed = "permission_request_failed"
    case calendarUnavailable = "calendar_unavailable"
    case calendarScopeMismatch = "calendar_scope_mismatch"
    case catalogTooLarge = "catalog_too_large"
    case calendarDataInvalid = "calendar_data_invalid"
    case calendarReadFailed = "calendar_read_failed"
    case responseTooLarge = "response_too_large"
    case operationTimeout = "operation_timeout"
    case platformUnavailable = "platform_unavailable"
    case imageUnavailable = "image_unavailable", imageInvalid = "image_invalid"
    case ocrFailed = "ocr_failed"
    case calendarNotWritable = "calendar_not_writable"
    case scheduleConflict = "schedule_conflict"
    case schedulePreflightFailed = "schedule_preflight_failed"
    case operationUnknown = "operation_unknown"
}

private struct Selection: Decodable, Hashable {
    let source_id: String
    let calendar_id: String
}

private struct Request: Decodable {
    let schema_version: Int
    let request_id: UUID
    let operation: Operation
    let selected_calendars: [Selection]?
    let start: String?
    let end: String?
    let time_zone: String?
    let max_events: Int?
    let image_path: String?
    let target: Selection?
    let rows: [ScheduleRow]?
    let plan_fingerprint: String?
}

private struct ReadScope {
    let selected: [Selection]
    let start: Date
    let end: Date
    let zone: TimeZone
    let limit: Int
}

private struct CalendarDescriptor: Encodable {
    let source_id: String
    let calendar_id: String
    let title: String
    let source_title: String
    let writable: Bool
    let allows_modification: Bool
    let supports_events: Bool
    let source_kind: String
}

private func deriveEligibility(allowsModification: Bool, supportsEvents: Bool) -> Bool {
    allowsModification && supportsEvents
}

private struct ScheduleRow: Decodable {
    let row_id: UUID
    let title: String
    let start: String
    let end: String
    let time_zone: String
    let owned_token: String
}

private struct ParsedScheduleRow {
    let row: ScheduleRow
    let start: Date
    let end: Date
    let zone: TimeZone
}

private struct ScheduleScope {
    let target: Selection
    let rows: [ParsedScheduleRow]
    let fingerprint: String
}

private enum ScheduleOutcome: String, Encodable {
    case absent, ownedExact = "owned_exact", conflict, created, unknown
}

private struct ScheduleResult: Encodable {
    let row_id: String
    let owned_token: String
    let outcome: ScheduleOutcome
}

private struct OCRBounds: Encodable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

private func normalizedOCRBounds(_ rectangle: CGRect) throws -> OCRBounds {
    let x = Double(rectangle.origin.x)
    let y = Double(rectangle.origin.y)
    let width = Double(rectangle.size.width)
    let height = Double(rectangle.size.height)
    guard [x, y, width, height].allSatisfy({ $0.isFinite }),
          x >= 0, y >= 0, width > 0, height > 0,
          x + width <= 1, y + height <= 1 else { throw SafeError.ocrFailed }
    return OCRBounds(x: x, y: y, width: width, height: height)
}

private struct OCRLine: Encodable {
    let text: String
    let confidence: Float
    let bounds: OCRBounds
}

// A local-only projection used for duplicate checks; no event body is returned.
private struct ScheduleEventProbe {
    let title: String
    let start: Date
    let end: Date
    let zone: String?
    let url: String?
    let allDay: Bool
    let recurring: Bool
    let alarms: Bool
    let attendees: Bool
}

private struct EventRow: Encodable {
    let source_id: String
    let calendar_id: String
    let event_id: String
    let occurrence_start: String
    let title: String
    let start: String
    let end: String
    let all_day: Bool
    let start_date: String?
    let end_date: String?
    let time_zone: String
    let status: String

    private enum CodingKeys: String, CodingKey {
        case source_id, calendar_id, event_id, occurrence_start, title, start, end
        case all_day, start_date, end_date, time_zone, status
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(source_id, forKey: .source_id)
        try container.encode(calendar_id, forKey: .calendar_id)
        try container.encode(event_id, forKey: .event_id)
        try container.encode(occurrence_start, forKey: .occurrence_start)
        try container.encode(title, forKey: .title)
        try container.encode(start, forKey: .start)
        try container.encode(end, forKey: .end)
        try container.encode(all_day, forKey: .all_day)
        try container.encode(start_date, forKey: .start_date)
        try container.encode(end_date, forKey: .end_date)
        try container.encode(time_zone, forKey: .time_zone)
        try container.encode(status, forKey: .status)
    }
}

private struct Reply: Encodable {
    let schema_version = 1
    let request_id: String
    let operation: Operation
    let status: String
    let access: Access
    let error: SafeError?
    let observed_at: String
    var calendars: [CalendarDescriptor]? = nil
    var events: [EventRow]? = nil
    var partial: Bool? = nil
    var plan_fingerprint: String? = nil
    var schedule_results: [ScheduleResult]? = nil
    var ocr_lines: [OCRLine]? = nil
    var ocr_partial: Bool? = nil

    private enum CodingKeys: String, CodingKey {
        case schema_version, request_id, operation, status, access, error, observed_at
        case calendars, events, partial, plan_fingerprint, schedule_results, ocr_lines, ocr_partial
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schema_version, forKey: .schema_version)
        try container.encode(request_id, forKey: .request_id)
        try container.encode(operation, forKey: .operation)
        try container.encode(status, forKey: .status)
        try container.encode(access, forKey: .access)
        try container.encode(error, forKey: .error)
        try container.encode(observed_at, forKey: .observed_at)
        try container.encodeIfPresent(calendars, forKey: .calendars)
        try container.encodeIfPresent(events, forKey: .events)
        try container.encodeIfPresent(partial, forKey: .partial)
        try container.encodeIfPresent(plan_fingerprint, forKey: .plan_fingerprint)
        try container.encodeIfPresent(schedule_results, forKey: .schedule_results)
        try container.encodeIfPresent(ocr_lines, forKey: .ocr_lines)
        try container.encodeIfPresent(ocr_partial, forKey: .ocr_partial)
    }
}

private func utc(_ date: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: date)
}

private func identifier(_ value: String) -> Bool {
    !value.isEmpty && value.unicodeScalars.count <= 512
        && value == value.trimmingCharacters(in: .whitespacesAndNewlines)
        && value != "all" && !value.contains("*")
        && !value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
}

private func title(_ raw: String) -> (value: String, clipped: Bool) {
    let cleaned = raw.unicodeScalars.map {
        CharacterSet.controlCharacters.contains($0) ? " " : String($0)
    }.joined().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    return (String(String.UnicodeScalarView(cleaned.unicodeScalars.prefix(512))),
            cleaned.unicodeScalars.count > 512)
}

private func knownZone(_ name: String) -> TimeZone? {
    guard name == "UTC" || TimeZone.knownTimeZoneIdentifiers.contains(name) else { return nil }
    return TimeZone(identifier: name)
}

private func awareDate(_ value: String) -> Date? {
    let pattern = #"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$"#
    guard value.utf8.count <= 64,
          let regex = try? NSRegularExpression(pattern: pattern),
          let match = regex.firstMatch(in: value, range: NSRange(value.startIndex..., in: value))
    else { return nil }
    let fields: [Int] = (1...6).compactMap { index in
        guard let range = Range(match.range(at: index), in: value) else { return nil }
        return Int(value[range])
    }
    guard fields.count == 6, fields[0] >= 1, fields[3] < 24, fields[4] < 60, fields[5] < 60,
          let zoneRange = Range(match.range(at: 7), in: value) else { return nil }
    let suffix = String(value[zoneRange])
    var offset = 0
    if suffix != "Z" {
        let pieces = suffix.dropFirst().split(separator: ":")
        guard pieces.count == 2, let hours = Int(pieces[0]), let minutes = Int(pieces[1]),
              hours < 24, minutes < 60 else { return nil }
        offset = (hours * 3600 + minutes * 60) * (suffix.first == "-" ? -1 : 1)
    }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = value.contains(".")
        ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
    guard let date = formatter.date(from: value), let zone = TimeZone(secondsFromGMT: offset)
    else { return nil }
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = zone
    let actual = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    guard [actual.year, actual.month, actual.day, actual.hour, actual.minute, actual.second]
        .compactMap({ $0 }) == fields else { return nil }
    return date
}

private func integerJSON(_ value: Any?) -> Bool {
    guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
        return false
    }
    return ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"]
        .contains(String(cString: number.objCType))
}

// JSONSerialization/JSONDecoder otherwise silently accept repeated or escaped-equivalent keys.
// Syntax is checked by JSONSerialization first; this bounded walk enforces object-key uniqueness.
private struct JSONKeyGuard {
    private let bytes: [UInt8]
    private var index = 0

    init(_ data: Data) { bytes = Array(data) }

    mutating func validate() throws {
        try value(depth: 0)
        whitespace()
        guard index == bytes.count else { throw SafeError.invalidRequest }
    }

    private mutating func whitespace() {
        while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 }
    }

    private mutating func take(_ byte: UInt8) throws {
        whitespace()
        guard index < bytes.count, bytes[index] == byte else { throw SafeError.invalidRequest }
        index += 1
    }

    private mutating func string() throws -> String {
        whitespace()
        let start = index
        try take(34)
        var escaped = false
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if escaped { escaped = false; continue }
            if byte == 92 { escaped = true; continue }
            if byte == 34 {
                return try JSONDecoder().decode(String.self, from: Data(bytes[start..<index]))
            }
        }
        throw SafeError.invalidRequest
    }

    private mutating func value(depth: Int) throws {
        guard depth <= 16 else { throw SafeError.invalidRequest }
        whitespace()
        guard index < bytes.count else { throw SafeError.invalidRequest }
        switch bytes[index] {
        case 123:
            index += 1; whitespace()
            if index < bytes.count && bytes[index] == 125 { index += 1; return }
            var seen: Set<String> = []
            while true {
                guard seen.insert(try string()).inserted else { throw SafeError.invalidRequest }
                try take(58)
                try value(depth: depth + 1)
                whitespace()
                guard index < bytes.count else { throw SafeError.invalidRequest }
                if bytes[index] == 125 { index += 1; return }
                try take(44)
            }
        case 91:
            index += 1; whitespace()
            if index < bytes.count && bytes[index] == 93 { index += 1; return }
            while true {
                try value(depth: depth + 1)
                whitespace()
                guard index < bytes.count else { throw SafeError.invalidRequest }
                if bytes[index] == 93 { index += 1; return }
                try take(44)
            }
        case 34:
            _ = try string()
        default:
            let start = index
            while index < bytes.count && ![9, 10, 13, 32, 44, 93, 125].contains(bytes[index]) {
                index += 1
            }
            guard index > start else { throw SafeError.invalidRequest }
        }
    }
}

private func readRequest() throws -> Request {
    var payload = Data()
    while let block = try FileHandle.standardInput.read(upToCount: min(8192, maximumInput + 1 - payload.count)),
          !block.isEmpty {
        payload.append(block)
        guard payload.count <= maximumInput else { throw SafeError.invalidRequest }
    }
    guard let raw = try JSONSerialization.jsonObject(with: payload) as? [String: Any],
          integerJSON(raw["schema_version"]) else { throw SafeError.invalidRequest }
    var keyGuard = JSONKeyGuard(payload)
    try keyGuard.validate()
    let request = try JSONDecoder().decode(Request.self, from: payload)
    guard request.schema_version == 1, request.request_id.uuidString != zeroRequestID else {
        throw SafeError.invalidRequest
    }
    switch request.operation {
    case .readEvents:
        guard Set(raw.keys) == ["schema_version", "request_id", "operation", "selected_calendars",
              "start", "end", "time_zone", "max_events"], integerJSON(raw["max_events"]),
              let selected = raw["selected_calendars"] as? [[String: Any]],
              selected.allSatisfy({ Set($0.keys) == ["source_id", "calendar_id"] })
        else { throw SafeError.invalidRequest }
    case .recognizeScheduleImage:
        guard Set(raw.keys) == ["schema_version", "request_id", "operation", "image_path"] else {
            throw SafeError.invalidRequest
        }
    case .preflightSchedule, .createSchedule, .reconcileSchedule:
        guard Set(raw.keys) == ["schema_version", "request_id", "operation", "target", "rows",
              "plan_fingerprint"],
              let target = raw["target"] as? [String: Any],
              Set(target.keys) == ["source_id", "calendar_id"],
              let rows = raw["rows"] as? [[String: Any]],
              rows.allSatisfy({ Set($0.keys) == ["row_id", "title", "start", "end", "time_zone",
                  "owned_token"] }) else { throw SafeError.invalidRequest }
    case .status, .requestAccess, .listCalendars:
        guard Set(raw.keys) == ["schema_version", "request_id", "operation"] else {
            throw SafeError.invalidRequest
        }
    }
    return request
}

private func lowerHash(_ value: String) -> Bool {
    value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
}

private func offsetMatches(_ text: String, instant: Date, zone: TimeZone) -> Bool {
    if text.hasSuffix("Z") { return zone.secondsFromGMT(for: instant) == 0 }
    let suffix = text.suffix(6)
    guard suffix.count == 6, suffix.first == "+" || suffix.first == "-" else { return false }
    let pieces = suffix.dropFirst().split(separator: ":")
    guard pieces.count == 2, let hours = Int(pieces[0]), let minutes = Int(pieces[1]) else { return false }
    let offset = (hours * 3600 + minutes * 60) * (suffix.first == "-" ? -1 : 1)
    return offset == zone.secondsFromGMT(for: instant)
}

private func scheduleToken(title: String, start: Date, end: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.formatOptions = [.withInternetDateTime]
    let text = "satori-shift-v1\n" + formatter.string(from: start) + "\n"
        + formatter.string(from: end) + "\n" + title.precomposedStringWithCanonicalMapping
    return SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
}

private func scheduleScope(_ request: Request) throws -> ScheduleScope {
    guard let target = request.target, identifier(target.source_id), identifier(target.calendar_id),
          let rows = request.rows, (1...100).contains(rows.count),
          let fingerprint = request.plan_fingerprint, lowerHash(fingerprint) else {
        throw SafeError.invalidRequest
    }
    var parsed: [ParsedScheduleRow] = []
    var rowIDs: Set<UUID> = []
    var tokens: Set<String> = []
    var instants: Set<String> = []
    for row in rows {
        guard row.row_id.uuidString != zeroRequestID, rowIDs.insert(row.row_id).inserted,
              lowerHash(row.owned_token), tokens.insert(row.owned_token).inserted,
              !row.title.isEmpty, row.title.unicodeScalars.count <= 120,
              !row.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !row.title.unicodeScalars.contains(where: { $0.value < 32 || (127...159).contains($0.value) }),
              let start = awareDate(row.start), let end = awareDate(row.end),
              end > start, end.timeIntervalSince(start) <= 36 * 3600,
              !row.start.contains("."), !row.end.contains("."),
              let zone = knownZone(row.time_zone),
              offsetMatches(row.start, instant: start, zone: zone),
              offsetMatches(row.end, instant: end, zone: zone),
              row.owned_token == scheduleToken(title: row.title, start: start, end: end) else {
            throw SafeError.invalidRequest
        }
        // Distinct row IDs must not duplicate the same title and interval in one plan.
        let identity = try JSONEncoder().encode([row.title, utc(start), utc(end)]).base64EncodedString()
        guard instants.insert(identity).inserted else { throw SafeError.invalidRequest }
        parsed.append(ParsedScheduleRow(row: row, start: start, end: end, zone: zone))
    }
    guard let first = parsed.map(\.start).min(), let last = parsed.map(\.end).max(),
          last.timeIntervalSince(first) <= 62 * 86400 else { throw SafeError.invalidRequest }
    return ScheduleScope(target: target, rows: parsed, fingerprint: fingerprint)
}

private func readScope(_ request: Request) throws -> ReadScope {
    guard let selected = request.selected_calendars, !selected.isEmpty, selected.count <= 100,
          Set(selected).count == selected.count,
          selected.allSatisfy({ identifier($0.source_id) && identifier($0.calendar_id) }),
          let startText = request.start, let endText = request.end,
          let start = awareDate(startText), let end = awareDate(endText),
          end > start, end.timeIntervalSince(start) <= 31 * 86400,
          let zoneName = request.time_zone, let zone = knownZone(zoneName),
          let limit = request.max_events, (1...100).contains(limit)
    else { throw SafeError.invalidRequest }
    return ReadScope(selected: selected, start: start, end: end, zone: zone, limit: limit)
}

@available(macOS 14.0, *)
private func accessStatus() -> Access {
    switch EKEventStore.authorizationStatus(for: .event) {
    case .notDetermined: return .notDetermined
    case .restricted: return .restricted
    case .denied: return .denied
    case .writeOnly: return .writeOnly
    case .fullAccess: return .fullAccess
    @unknown default: return .unknown
    }
}

private func accessError(_ access: Access) -> SafeError {
    switch access {
    case .denied: return .permissionDenied
    case .restricted: return .permissionRestricted
    default: return .permissionRequired
    }
}

// Native/vendor diagnostics never share the one reserved protocol descriptor.
private final class Output: @unchecked Sendable {
    private let lock = NSLock()
    private let destination: FileHandle
    private var claimed = false
    private var sent = false
    private var requestID = zeroRequestID
    private var operation = Operation.status
    private var access = Access.unknown
    private var code: Int32 = 1

    init(descriptor: Int32) { destination = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true) }

    func context(_ request: Request, access: Access) {
        lock.lock(); defer { lock.unlock() }
        requestID = request.request_id.uuidString.lowercased()
        operation = request.operation
        self.access = access
    }

    func reply(error: SafeError? = nil) -> Reply {
        lock.lock(); defer { lock.unlock() }
        let failure = error == .operationTimeout && operation == .createSchedule
            ? SafeError.operationUnknown : error
        return Reply(request_id: requestID, operation: operation,
                     status: failure == nil ? "ok" : "error", access: access,
                     error: failure, observed_at: utc(Date()))
    }

    func send(_ response: Reply) {
        lock.lock()
        guard !claimed else { lock.unlock(); return }
        claimed = true
        code = response.error == nil ? 0 : 1
        lock.unlock()
        do {
            var body = try JSONEncoder().encode(response)
            if body.count + 1 > maximumOutput {
                body = try JSONEncoder().encode(reply(error: .responseTooLarge))
                lock.lock(); code = 1; lock.unlock()
            }
            body.append(0x0A)
            try destination.write(contentsOf: body)
        } catch { /* Closed pipe/encoding failure never exposes underlying diagnostics. */ }
        lock.lock(); sent = true; lock.unlock()
    }

    var finished: Bool { lock.lock(); defer { lock.unlock() }; return sent }
    var isClaimed: Bool { lock.lock(); defer { lock.unlock() }; return claimed }
    var exitCode: Int32 { lock.lock(); defer { lock.unlock() }; return code }
}

private func sourceKind(_ source: EKSource) -> String {
    switch source.sourceType {
    case .local: return "local"
    case .exchange: return "exchange"
    case .calDAV: return "caldav"
    case .mobileMe: return "mobileme"
    case .subscribed: return "subscribed"
    case .birthdays: return "birthdays"
    @unknown default: return "unknown"
    }
}

@available(macOS 14.0, *)
private func calendars(_ store: EKEventStore) throws -> [CalendarDescriptor] {
    let available = store.calendars(for: .event)
    guard available.count <= 100 else { throw SafeError.catalogTooLarge }
    var result: [CalendarDescriptor] = []
    for calendar in available {
        guard let source = calendar.source, identifier(source.sourceIdentifier),
              identifier(calendar.calendarIdentifier) else { throw SafeError.calendarDataInvalid }
        let calendarTitle = title(calendar.title).value
        let sourceTitle = title(source.title).value
        guard !calendarTitle.isEmpty, !sourceTitle.isEmpty else { throw SafeError.calendarDataInvalid }
        let allowsModification = calendar.allowsContentModifications
        let supportsEvents = calendar.allowedEntityTypes.contains(.event)
        result.append(CalendarDescriptor(source_id: source.sourceIdentifier,
            calendar_id: calendar.calendarIdentifier, title: calendarTitle,
            source_title: sourceTitle,
            writable: deriveEligibility(allowsModification: allowsModification, supportsEvents: supportsEvents),
            allows_modification: allowsModification, supports_events: supportsEvents,
            source_kind: sourceKind(source)))
    }
    guard accessStatus() == .fullAccess else { throw accessError(accessStatus()) }
    return result.sorted { ($0.source_id, $0.calendar_id) < ($1.source_id, $1.calendar_id) }
}

private func dateOnly(_ date: Date, zone: TimeZone) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.timeZone = zone
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.string(from: date)
}

private func floatingDay(_ date: Date, returnedZone: TimeZone, eventZone: TimeZone) -> Date? {
    var returnedCalendar = Calendar(identifier: .gregorian)
    returnedCalendar.timeZone = returnedZone
    let components = returnedCalendar.dateComponents([.year, .month, .day], from: date)
    var targetCalendar = Calendar(identifier: .gregorian)
    targetCalendar.timeZone = eventZone
    guard let result = targetCalendar.date(from: components),
          dateOnly(result, zone: eventZone) == dateOnly(date, zone: returnedZone) else { return nil }
    return result
}

@available(macOS 14.0, *)
private func events(_ scope: ReadScope, store: EKEventStore, output: Output) throws -> Reply {
    var selected: [EKCalendar] = []
    for item in scope.selected {
        guard let calendar = store.calendar(withIdentifier: item.calendar_id) else {
            throw SafeError.calendarUnavailable
        }
        guard calendar.source?.sourceIdentifier == item.source_id else {
            throw SafeError.calendarScopeMismatch
        }
        guard calendar.allowedEntityTypes.contains(.event) else { throw SafeError.calendarUnavailable }
        selected.append(calendar)
    }
    guard !selected.isEmpty else { throw SafeError.invalidRequest }
    // Never pass nil/empty calendars: EventKit interprets nil as all calendars.
    let predicate = store.predicateForEvents(withStart: scope.start, end: scope.end, calendars: selected)
    let fetched = store.events(matching: predicate).sorted {
        ($0.startDate ?? .distantPast, $0.eventIdentifier ?? "", $0.occurrenceDate ?? .distantPast)
            < ($1.startDate ?? .distantPast, $1.eventIdentifier ?? "", $1.occurrenceDate ?? .distantPast)
    }
    let allowed = Set(scope.selected)
    var rows: [EventRow] = []
    var seen: Set<String> = []
    var partial = false
    for event in fetched {
        if event.status == .canceled { continue }
        guard let calendar = event.calendar, let source = calendar.source,
              allowed.contains(Selection(source_id: source.sourceIdentifier,
                                         calendar_id: calendar.calendarIdentifier)) else {
            throw SafeError.calendarScopeMismatch
        }
        guard let eventID = event.eventIdentifier, identifier(eventID),
              let start = event.startDate, let end = event.endDate,
              start.timeIntervalSince1970.isFinite, end.timeIntervalSince1970.isFinite,
              end > start else {
            throw SafeError.calendarDataInvalid
        }
        let eventZone = event.timeZone ?? scope.zone
        guard knownZone(eventZone.identifier) != nil else { throw SafeError.calendarDataInvalid }
        var actualStart = start
        var actualEnd = end
        let startDate = event.isAllDay ? dateOnly(start, zone: scope.zone) : nil
        let endDate = event.isAllDay ? dateOnly(end, zone: scope.zone) : nil
        if event.isAllDay {
            guard let startDate, let endDate, startDate < endDate,
                  let civilStart = floatingDay(start, returnedZone: scope.zone, eventZone: eventZone),
                  let civilEnd = floatingDay(end, returnedZone: scope.zone, eventZone: eventZone) else {
                throw SafeError.calendarDataInvalid
            }
            actualStart = civilStart
            actualEnd = civilEnd
        }
        guard actualEnd > actualStart else { throw SafeError.calendarDataInvalid }
        if actualStart >= scope.end || actualEnd <= scope.start { continue }
        var occurrenceDate = event.occurrenceDate ?? start
        guard occurrenceDate.timeIntervalSince1970.isFinite else { throw SafeError.calendarDataInvalid }
        if event.isAllDay {
            guard let civilOccurrence = floatingDay(occurrenceDate, returnedZone: scope.zone,
                                                   eventZone: eventZone) else {
                throw SafeError.calendarDataInvalid
            }
            occurrenceDate = civilOccurrence
        }
        let occurrence = utc(occurrenceDate)
        // Canonical JSON avoids collisions if opaque identifiers contain separators.
        let identity = try JSONEncoder().encode([source.sourceIdentifier,
            calendar.calendarIdentifier, eventID, occurrence]).base64EncodedString()
        if !seen.insert(identity).inserted { continue }
        if rows.count == scope.limit { partial = true; break }
        let renderedTitle = title(event.title ?? "")
        let row = EventRow(source_id: source.sourceIdentifier, calendar_id: calendar.calendarIdentifier,
            event_id: eventID, occurrence_start: occurrence, title: renderedTitle.value,
            start: utc(actualStart), end: utc(actualEnd), all_day: event.isAllDay,
            start_date: startDate, end_date: endDate, time_zone: eventZone.identifier,
            status: event.status == .tentative ? "tentative" : "confirmed")
        rows.append(row)
        var candidate = output.reply()
        candidate.events = rows
        candidate.partial = true
        if try JSONEncoder().encode(candidate).count + 1 > maximumOutput - 256 {
            rows.removeLast()
            partial = true
            break
        }
        partial = partial || renderedTitle.clipped
    }
    guard accessStatus() == .fullAccess else { throw accessError(accessStatus()) }
    var reply = output.reply()
    reply.events = rows
    reply.partial = partial
    return reply
}

private func recognizeScheduleImage(_ request: Request, output: Output) throws -> Reply {
    let limit = 20 * 1024 * 1024
    guard let path = request.image_path, !path.isEmpty, path.utf8.count <= 4096,
          path.hasPrefix("/"),
          !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
        throw SafeError.invalidRequest
    }
    let imageURL = URL(fileURLWithPath: path)
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: path),
          attributes[.type] as? FileAttributeType == .typeRegular,
          let fileSize = attributes[.size] as? NSNumber,
          fileSize.int64Value > 0, fileSize.int64Value <= Int64(limit),
          let file = try? FileHandle(forReadingFrom: imageURL) else { throw SafeError.imageUnavailable }
    defer { try? file.close() }
    var bytes = Data()
    do {
        while let block = try file.read(upToCount: min(64 * 1024, limit + 1 - bytes.count)),
              !block.isEmpty {
            bytes.append(block)
            guard bytes.count <= limit else { throw SafeError.imageInvalid }
        }
    } catch let failure as SafeError { throw failure }
    catch { throw SafeError.imageUnavailable }
    guard !bytes.isEmpty, bytes.count == fileSize.intValue,
          let source = CGImageSourceCreateWithData(bytes as CFData,
              [kCGImageSourceShouldCache: false] as CFDictionary),
          CGImageSourceGetCount(source) == 1,
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
          let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
          width.int64Value > 0, height.int64Value > 0,
          width.int64Value <= 20_000, height.int64Value <= 20_000,
          width.int64Value * height.int64Value <= 60_000_000,
          let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
              kCGImageSourceCreateThumbnailFromImageAlways: true,
              kCGImageSourceCreateThumbnailWithTransform: true,
              kCGImageSourceThumbnailMaxPixelSize: 4096,
              kCGImageSourceShouldCacheImmediately: true,
          ] as CFDictionary) else { throw SafeError.imageInvalid }
    let recognition = VNRecognizeTextRequest()
    recognition.recognitionLevel = .accurate
    guard let supported = try? recognition.supportedRecognitionLanguages() else { throw SafeError.ocrFailed }
    let requested = ["ru-RU", "en-US"]
    let languages = requested.filter { supported.contains($0) }
    guard !languages.isEmpty else { throw SafeError.ocrFailed }
    recognition.recognitionLanguages = languages
    recognition.usesLanguageCorrection = false
    do { try VNImageRequestHandler(cgImage: image, options: [:]).perform([recognition]) }
    catch { throw SafeError.ocrFailed }
    // Vision text is a transient observation, never a parsed calendar plan or instruction.
    let observations = (recognition.results ?? []).sorted {
        ($0.boundingBox.midY, -$0.boundingBox.minX) > ($1.boundingBox.midY, -$1.boundingBox.minX)
    }
    var lines: [OCRLine] = []
    var partial = observations.count > 200 || languages.count != requested.count
    var byteCount = 0
    for observation in observations.prefix(200) {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let projection = title(candidate.string)
        if projection.value.isEmpty { continue }
        guard candidate.confidence.isFinite else { throw SafeError.ocrFailed }
        byteCount += projection.value.utf8.count
        if byteCount > 64 * 1024 { partial = true; break }
        let bounds = try normalizedOCRBounds(observation.boundingBox)
        lines.append(OCRLine(text: projection.value,
            confidence: max(0, min(1, candidate.confidence)), bounds: bounds))
        partial = partial || projection.clipped
    }
    var reply = output.reply()
    reply.ocr_lines = lines
    reply.ocr_partial = partial
    return reply
}

private func scheduleMarker(_ token: String) -> String { "satori-calendar://shift/v1/" + token }

private func classifyScheduleRow(_ row: ParsedScheduleRow, probes: [ScheduleEventProbe]) -> ScheduleOutcome {
    let marker = scheduleMarker(row.row.owned_token)
    let normalizedTitle = row.row.title.precomposedStringWithCanonicalMapping
    let owned = probes.filter { $0.url == marker }
    let equivalent = probes.filter {
        $0.title.precomposedStringWithCanonicalMapping == normalizedTitle
            && $0.start == row.start && $0.end == row.end
    }
    if owned.count == 1, let match = owned.first,
       match.title.precomposedStringWithCanonicalMapping == normalizedTitle,
       match.start == row.start, match.end == row.end, !match.allDay, !match.recurring,
       !match.alarms, !match.attendees, equivalent.count == 1 {
        return .ownedExact
    }
    return !owned.isEmpty || !equivalent.isEmpty ? .conflict : .absent
}

@available(macOS 14.0, *)
private func scheduleCalendar(_ target: Selection, store: EKEventStore) throws -> EKCalendar {
    guard accessStatus() == .fullAccess else { throw accessError(accessStatus()) }
    guard let calendar = store.calendar(withIdentifier: target.calendar_id) else {
        throw SafeError.calendarUnavailable
    }
    guard calendar.source?.sourceIdentifier == target.source_id else {
        throw SafeError.calendarScopeMismatch
    }
    guard calendar.allowedEntityTypes.contains(.event), calendar.allowsContentModifications else {
        throw SafeError.calendarNotWritable
    }
    return calendar
}

@available(macOS 14.0, *)
private func preflightSchedule(_ scope: ScheduleScope, store: EKEventStore) throws -> [ScheduleResult] {
    let calendar = try scheduleCalendar(scope.target, store: store)
    var result: [ScheduleResult] = []
    var inspected = 0
    for row in scope.rows {
        // Each predicate is confined to one approved row interval and one exact calendar.
        let predicate = store.predicateForEvents(withStart: row.start, end: row.end,
                                                calendars: [calendar])
        let events = store.events(matching: predicate)
        inspected += events.count
        guard events.count <= 1000, inspected <= 5000 else { throw SafeError.schedulePreflightFailed }
        var probes: [ScheduleEventProbe] = []
        for event in events {
            guard event.calendar?.calendarIdentifier == scope.target.calendar_id,
                  event.calendar?.source?.sourceIdentifier == scope.target.source_id else {
                throw SafeError.calendarScopeMismatch
            }
            if event.status == .canceled { continue }
            guard let start = event.startDate, let end = event.endDate,
                  start.timeIntervalSince1970.isFinite, end.timeIntervalSince1970.isFinite,
                  end > start else { throw SafeError.schedulePreflightFailed }
            guard start < row.end, end > row.start else { continue }
            probes.append(ScheduleEventProbe(title: event.title ?? "", start: start, end: end,
                zone: event.timeZone?.identifier, url: event.url?.absoluteString,
                allDay: event.isAllDay, recurring: event.hasRecurrenceRules,
                alarms: !(event.alarms ?? []).isEmpty, attendees: !(event.attendees ?? []).isEmpty))
        }
        result.append(ScheduleResult(row_id: row.row.row_id.uuidString.lowercased(),
            owned_token: row.row.owned_token, outcome: classifyScheduleRow(row, probes: probes)))
    }
    guard accessStatus() == .fullAccess else { throw accessError(accessStatus()) }
    return result
}

@available(macOS 14.0, *)
private func createSchedule(_ scope: ScheduleScope, store: EKEventStore, output: Output) throws -> [ScheduleResult] {
    let before = try preflightSchedule(scope, store: store)
    // No automatic overwrite, duplicate skip or replay: a changed plan needs a new review.
    guard before.allSatisfy({ $0.outcome == .absent }) else { throw SafeError.scheduleConflict }
    let calendar = try scheduleCalendar(scope.target, store: store)
    do {
        var staged: [EKEvent] = []
        for row in scope.rows {
            guard !output.isClaimed else { throw SafeError.operationUnknown }
            _ = try scheduleCalendar(scope.target, store: store)
            let event = EKEvent(eventStore: store)
            event.calendar = calendar
            event.title = row.row.title
            event.startDate = row.start
            event.endDate = row.end
            event.timeZone = row.zone
            event.isAllDay = false
            event.url = URL(string: scheduleMarker(row.row.owned_token))
            event.alarms = []
            event.recurrenceRules = []
            // Fresh events have no attendee/organizer, notes, location or attachments.
            try store.save(event, span: .thisEvent, commit: false)
            staged.append(event)
        }
        guard !output.isClaimed else { throw SafeError.operationUnknown }
        _ = try scheduleCalendar(scope.target, store: store)
        try store.commit()
        _ = staged
        // A fresh store prevents uncommitted in-memory objects from being mistaken for receipts.
        let observed = try preflightSchedule(scope, store: EKEventStore())
        guard observed.allSatisfy({ $0.outcome == .ownedExact }) else { throw SafeError.operationUnknown }
        return observed.map { ScheduleResult(row_id: $0.row_id, owned_token: $0.owned_token, outcome: .created) }
    } catch {
        // reset drops this store's pending edits; it is not an external deletion or rollback claim.
        store.reset()
        throw SafeError.operationUnknown
    }
}

@available(macOS 14.0, *)
private func handle(_ request: Request, output: Output) throws {
    if request.operation == .recognizeScheduleImage {
        // Local OCR does not even inspect TCC Calendar status.
        output.context(request, access: .unknown)
        output.send(try recognizeScheduleImage(request, output: output))
        return
    }
    let access = accessStatus()
    output.context(request, access: access)
    switch request.operation {
    case .status:
        // No EKEventStore instance, calendars or events are opened for status.
        output.send(output.reply())
    case .requestAccess:
        if access == .fullAccess { output.send(output.reply()); return }
        if access == .denied || access == .restricted { throw accessError(access) }
        guard let usage = Bundle.main.object(forInfoDictionaryKey: "NSCalendarsFullAccessUsageDescription")
                as? String, !usage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw SafeError.platformUnavailable
        }
        let store = EKEventStore()
        store.requestFullAccessToEvents { granted, error in
            DispatchQueue.main.async {
                _ = store // Retain this request's store until its asynchronous completion.
                let current = accessStatus()
                output.context(request, access: current)
                let failure: SafeError? = granted && current == .fullAccess ? nil
                    : (error == nil ? accessError(current) : .permissionRequestFailed)
                output.send(output.reply(error: failure))
            }
        }
    case .listCalendars:
        guard access == .fullAccess else { throw accessError(access) }
        var reply = output.reply()
        reply.calendars = try calendars(EKEventStore())
        reply = Reply(request_id: reply.request_id, operation: reply.operation, status: reply.status,
            access: reply.access, error: reply.error, observed_at: utc(Date()), calendars: reply.calendars)
        output.send(reply)
    case .readEvents:
        // Validate the full explicit scope before opening a store or fetching any data.
        let scope = try readScope(request)
        guard access == .fullAccess else { throw accessError(access) }
        // EventKit returns floating all-day dates in the process default timezone.
        // This is confined to one owned child and never changes the OS timezone.
        NSTimeZone.default = scope.zone
        output.send(try events(scope, store: EKEventStore(), output: output))
    case .recognizeScheduleImage:
        return // Handled before any Calendar API above.
    case .preflightSchedule, .createSchedule, .reconcileSchedule:
        let scope = try scheduleScope(request)
        guard access == .fullAccess else { throw accessError(access) }
        let store = EKEventStore()
        let results = request.operation == .createSchedule
            ? try createSchedule(scope, store: store, output: output)
            : try preflightSchedule(scope, store: store)
        var reply = output.reply()
        reply.plan_fingerprint = scope.fingerprint
        reply.schedule_results = results
        output.send(reply)
    }
}

private func main() -> Int32 {
    let descriptor = dup(STDOUT_FILENO)
    guard descriptor >= 0 else { return 1 }
    let sink = open("/dev/null", O_WRONLY)
    guard sink >= 0 else { close(descriptor); return 1 }
    _ = dup2(sink, STDOUT_FILENO)
    _ = dup2(sink, STDERR_FILENO)
    close(sink)
    let output = Output(descriptor: descriptor)
    // A second, lock-independent watchdog guarantees termination even if native I/O hangs.
    let deadline = DispatchWorkItem { Darwin._exit(1) }
    DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 90, execute: deadline)
    let timeout = DispatchWorkItem { output.send(output.reply(error: .operationTimeout)) }
    DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + 89, execute: timeout)
    defer { timeout.cancel(); deadline.cancel() }
    do {
        let request = try readRequest()
        if #available(macOS 14.0, *) {
            output.context(request, access: .unknown)
            try handle(request, output: output)
        } else {
            output.context(request, access: .unknown)
            throw SafeError.platformUnavailable
        }
    } catch let failure as SafeError {
        output.send(output.reply(error: failure))
    } catch {
        output.send(output.reply(error: .invalidRequest))
    }
    while !output.finished {
        _ = RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
    }
    return output.exitCode
}

exit(main())
