import Foundation
import MapKit

@MainActor
func runSelfTests() {
    var count = 0
    func check(_ condition: @autoclosure () -> Bool) { precondition(condition()); count += 1 }
    let point = Coordinate(latitude: 59.9398, longitude: 30.3146)
    let wall = ISO8601DateFormatter().date(from: "2026-10-31T12:00:00Z")!
    func input(_ city: String = "SYNTHETIC city", _ epoch: Int = 0, _ center: Coordinate? = nil) -> SearchInput {
        SearchInput(schemaVersion: 1, requestID: "synthetic-request", sessionID: "synthetic-session", epoch: epoch, city: city, district: "SYNTHETIC district", query: "SYNTHETIC query", radiusMeters: 2000, explicitCenter: center)
    }
    let place = PlaceRecord(placeID: "synthetic-place", name: "SYNTHETIC", address: "SYNTHETIC", coordinate: point, distanceMeters: 0, cuisineStatus: "unknown", hoursStatus: "unknown", budgetStatus: "unknown")
    func output(_ origin: Coordinate? = point, _ confirmed: Bool = true, _ visible: Bool = true, _ status: String = "complete", _ epoch: Int = 0) -> SearchOutput {
        SearchOutput(schemaVersion: 1, requestID: "synthetic-request", sessionID: "synthetic-session", epoch: epoch, receiptID: "synthetic-receipt", status: status, source: "apple_mapkit", observedAt: "2026-10-31T12:00:00Z", expiresAt: "2026-10-31T12:01:00Z", city: "SYNTHETIC city", district: "SYNTHETIC district", query: "SYNTHETIC query", originLabel: "SYNTHETIC", origin: origin, originConfirmed: confirmed, places: [place], areaChoices: [], mapVisible: visible, errorCode: nil)
    }
    check(input().valid()); check(!input("").valid()); check(!input("   ").valid()); check(!input("a\nb").valid())
    check(!input(String(repeating: "a", count: 121)).valid())
    check(!Coordinate(latitude: .nan, longitude: 0).valid)
    check(!Coordinate(latitude: 91, longitude: 0).valid)
    check(!input("synthetic", -1).valid())
    check(input("synthetic", 0, point).valid())
    var lease = PlacesLease()
    let ticket = lease.begin(input(), now: 0)!
    check(lease.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 0, wallNow: wall) == nil)
    check(lease.admit(ticket, output: output(), now: 1, wallNow: wall))
    check(lease.route(receiptID: "wrong", placeID: place.placeID, now: 2, wallNow: wall) == nil)
    check(lease.route(receiptID: "synthetic-receipt", placeID: "wrong", now: 2, wallNow: wall) == nil)
    check(lease.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 2, wallNow: wall) == place)
    check(lease.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 61, wallNow: wall) == nil)
    for unsafe in [output(nil), output(point, false), output(point, true, false), output(point, true, true, "needs_origin_confirmation"), output(point, true, true, "unavailable")] {
        var isolated = PlacesLease(); let token = isolated.begin(input(), now: 0)!
        check(isolated.admit(token, output: unsafe, now: 0, wallNow: wall))
        check(isolated.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 0, wallNow: wall) == nil)
    }
    for transition in ["cancel", "new-query", "epoch", "deadline"] {
        var isolated = PlacesLease(); let token = isolated.begin(input(), now: 0)!
        switch transition {
        case "cancel": isolated.cancel()
        case "new-query": _ = isolated.begin(input("SYNTHETIC other"), now: 0)
        case "epoch": check(!isolated.admit(token, output: output(point, true, true, "complete", 1), now: 0, wallNow: wall))
        default: break
        }
        check(!isolated.admit(token, output: output(), now: transition == "deadline" ? 15 : 16, wallNow: wall))
    }
    var rollback = PlacesLease(); let before = rollback.begin(input(), now: 10)!
    check(!rollback.admit(before, output: output(), now: 9, wallNow: wall))
    check(rollback.output == nil)
    check(straightDistance(point, point) == 0)
    check(straightDistance(point, Coordinate(latitude: 60, longitude: 30.3146)) > 6000)
    check(boundedPlain("SYNTHETIC\ntext", maxUTF8: 10).utf8.count <= 10)
    check(!boundedPlain("a\nb", maxUTF8: 10).contains("\n"))
    var lines = BoundedLineBuffer()
    check(lines.append(Data("{\"operation\":\"cancel\"}".utf8))?.isEmpty == true)
    check(lines.append(Data([10]))?.count == 1)
    check(parseCommand(Data("{\"operation\":\"cancel\"}".utf8))?.operation == "cancel")
    check(parseCommand(Data("{\"operation\":\"cancel\",\"clipboard\":true}".utf8)) == nil)
    check(parseCommand(Data("{\"operation\":\"current_location\"}".utf8)) == nil)
    check(parseCommand(Data("{\"operation\":\"route\",\"receiptID\":\"a\",\"placeID\":\"b\",\"actionID\":\"c\"}".utf8))?.operation == "route")
    check(parseCommand(Data("{\"operation\":\"route\",\"receiptID\":\"a\",\"placeID\":\"b\"}".utf8)) == nil)
    var over = BoundedLineBuffer(); check(over.append(Data(repeating: 65, count: maximumRequestBytes + 1)) == nil)
    var blank = BoundedLineBuffer(); check(blank.append(Data([10])) == nil)
    let searchObject: [String: Any] = ["operation": "search", "search": try! JSONSerialization.jsonObject(with: JSONEncoder().encode(input()))]
    check(parseCommand(try! JSONSerialization.data(withJSONObject: searchObject))?.search == input())
    let destinations = (0..<110).map { index in
        routeItems(origin: point, destination: Coordinate(latitude: point.latitude + Double(index) * 0.000001, longitude: point.longitude), destinationName: "SYNTHETIC \(index)")[1]
    }
    let (projected, partial) = projectPlaces(destinations, input: input(), origin: point)
    check(partial); check(projected.count == 20)
    check(projected.allSatisfy { $0.hoursStatus == "unknown" && $0.cuisineStatus == "unknown" && $0.budgetStatus == "unknown" })
    check(Set(projected.map(\.placeID)).count == projected.count)
    let duplicate = projectPlaces([destinations[0], destinations[0]], input: input(), origin: point)
    check(duplicate.0.count == 1); check(!duplicate.1)
    let far = routeItems(origin: point, destination: Coordinate(latitude: 65, longitude: 30), destinationName: "SYNTHETIC far")[1]
    check(projectPlaces([far], input: input(), origin: point).0.isEmpty)
    let endpoints = routeItems(origin: point, destination: Coordinate(latitude: 59.95, longitude: 30.32), destinationName: "SYNTHETIC destination")
    check(endpoints.count == 2); check(coordinateOf(endpoints[0]) == point)
    check(coordinateOf(endpoints[1]) == Coordinate(latitude: 59.95, longitude: 30.32))
    var fractional = PlacesLease(); let fractionalTicket = fractional.begin(input(), now: 0)!
    check(fractional.admit(fractionalTicket, output: output(), now: 0, wallNow: wall.addingTimeInterval(59.75)))
    check(fractional.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 0.25, wallNow: wall.addingTimeInterval(60)) == nil)
    var forwardWall = PlacesLease(); let forwardTicket = forwardWall.begin(input(), now: 0)!
    check(forwardWall.admit(forwardTicket, output: output(), now: 0, wallNow: wall))
    check(forwardWall.route(receiptID: "synthetic-receipt", placeID: place.placeID, now: 1, wallNow: wall.addingTimeInterval(61)) == nil)
    print("PASS: \(count) synthetic validation/lease/route/stream assertions; no MapKit request, Maps opening or UI")
}
