import Foundation

struct PlacesLease {
    struct Ticket: Equatable {
        let nonce: UUID
        let generation: Int
        let input: SearchInput
        let deadline: Double
    }
    private(set) var generation = 0
    private(set) var pending: Ticket?
    private(set) var output: SearchOutput?
    private var expires: Double?
    private var lastNow: Double?

    mutating func check(_ now: Double) -> Bool {
        guard now.isFinite, now >= 0, lastNow == nil || now >= lastNow! else { cancel(); return false }
        lastNow = now
        if let expires, now >= expires { cancel(); return false }
        return true
    }
    mutating func begin(_ input: SearchInput, now: Double) -> Ticket? {
        guard check(now), input.valid() else { return nil }
        cancel()
        let ticket = Ticket(nonce: UUID(), generation: generation, input: input, deadline: now + 15)
        pending = ticket
        return ticket
    }
    mutating func admit(_ ticket: Ticket, output: SearchOutput, now: Double, wallNow: Date = Date()) -> Bool {
        guard let advertisedExpiry = ISO8601DateFormatter().date(from: output.expiresAt), advertisedExpiry > wallNow else { cancel(); return false }
        guard check(now), pending == ticket, ticket.generation == generation,
              now < ticket.deadline, output.requestID == ticket.input.requestID,
              output.sessionID == ticket.input.sessionID, output.epoch == ticket.input.epoch else { return false }
        pending = nil
        self.output = output
        expires = now + min(60, advertisedExpiry.timeIntervalSince(wallNow))
        return true
    }
    mutating func route(receiptID: String, placeID: String, now: Double, wallNow: Date = Date()) -> PlaceRecord? {
        guard check(now), let output else { return nil }
        guard let advertisedExpiry = ISO8601DateFormatter().date(from: output.expiresAt), wallNow < advertisedExpiry else { cancel(); return nil }
        guard output.receiptID == receiptID, output.originConfirmed,
              output.origin != nil, output.mapVisible, ["complete", "partial"].contains(output.status) else { return nil }
        return output.places.first { $0.placeID == placeID }
    }
    mutating func cancel() {
        generation += 1
        pending = nil
        output = nil
        expires = nil
    }
}
