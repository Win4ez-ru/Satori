import Foundation

@MainActor
final class PlacesCoordinator {
    typealias Sink = @MainActor ([String: Any]) -> Void
    private let provider: PlacesProviderPort
    private let now: @MainActor () -> Double
    private let sink: Sink
    private let headless: Bool
    private var lease = PlacesLease()
    private var input: SearchInput?
    private var deadlineTask: Task<Void, Never>?
    private var routeActions = Set<String>()
    private var closed = false
    private lazy var mapWindow = PlacesMapWindow(
        onClose: { [weak self] in self?.cancel(reason: "map_closed") },
        onChooseArea: { [weak self] choice in self?.chooseArea(choice) },
        onRoute: { [weak self] receipt, place in self?.route(receiptID: receipt, placeID: place, actionID: UUID().uuidString) }
    )

    init(provider: PlacesProviderPort, headless: Bool = false, now: @escaping @MainActor () -> Double, sink: @escaping Sink) {
        self.provider = provider; self.headless = headless; self.now = now; self.sink = sink
    }

    func search(_ requested: SearchInput) {
        guard !closed else { return }
        cancel(reason: "superseded")
        guard requested.valid() else { sink(["event": "protocol_error", "code": "invalid_request"]); return }
        input = requested
        if !headless { mapWindow.showSearching(requested) }
        if let point = requested.explicitCenter {
            startPlaces(requested, origin: point, label: "Явно выбранная точка: \(requested.city), \(requested.district)")
            return
        }
        guard let ticket = lease.begin(requested, now: now()) else { return }
        scheduleDeadline(seconds: 15, generation: ticket.generation)
        provider.resolveArea(requested) { [weak self] choices, error in
            guard let self, !self.closed, self.lease.pending == ticket else { return }
            let status = choices?.isEmpty == false ? "needs_origin_confirmation" : "unavailable"
            let result = self.output(for: requested, status: status, origin: nil, label: nil, confirmed: false, places: [], choices: choices ?? [], error: error ?? (choices?.isEmpty == true ? "area_not_resolved" : nil))
            self.admit(ticket, result)
        }
    }

    private func chooseArea(_ choiceID: String) {
        guard !closed, lease.check(now()), let result = lease.output,
              result.status == "needs_origin_confirmation", let input,
              let choice = result.areaChoices.first(where: { $0.choiceID == choiceID }) else { return }
        // This method is wired only to the visible native selection button.
        startPlaces(input, origin: choice.coordinate, label: choice.label)
    }

    private func startPlaces(_ requested: SearchInput, origin: Coordinate, label: String) {
        provider.cancel()
        lease.cancel()
        guard let ticket = lease.begin(requested, now: now()) else { return }
        if !headless { mapWindow.showSearching(requested) }
        sink(binding("searching"))
        scheduleDeadline(seconds: 15, generation: ticket.generation)
        provider.search(requested, origin: origin) { [weak self] records, partial, error in
            guard let self, !self.closed, self.lease.pending == ticket else { return }
            let status = records == nil ? "unavailable" : partial ? "partial" : "complete"
            let result = self.output(for: requested, status: status, origin: origin, label: label, confirmed: true, places: records ?? [], choices: [], error: error)
            self.admit(ticket, result)
        }
    }

    private func output(for input: SearchInput, status: String, origin: Coordinate?, label: String?, confirmed: Bool, places: [PlaceRecord], choices: [AreaChoice], error: String?) -> SearchOutput {
        let date = Date()
        return SearchOutput(
            schemaVersion: 1, requestID: input.requestID, sessionID: input.sessionID, epoch: input.epoch,
            receiptID: UUID().uuidString, status: status, source: "apple_mapkit", observedAt: isoNow(date), expiresAt: isoNow(date.addingTimeInterval(60)),
            city: input.city, district: input.district, query: input.query,
            originLabel: label, origin: origin, originConfirmed: confirmed,
            places: places, areaChoices: choices,
            mapVisible: !headless && (origin != nil || !places.isEmpty || !choices.isEmpty), errorCode: error
        )
    }

    private func admit(_ ticket: PlacesLease.Ticket, _ result: SearchOutput) {
        guard lease.admit(ticket, output: result, now: now()) else { cancel(reason: "stale_result"); return }
        deadlineTask?.cancel()
        if !headless {
            mapWindow.showResult(result)
            guard !result.mapVisible || mapWindow.isPresenting else { cancel(reason: "map_not_visible"); return }
        }
        do {
            let object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(result))
            sink(["event": "search_result", "result": object])
        } catch { cancel(reason: "output_invalid"); return }
        let remaining = ISO8601DateFormatter().date(from: result.expiresAt)?.timeIntervalSinceNow ?? 0
        scheduleDeadline(seconds: max(0, min(60, remaining)), generation: ticket.generation)
    }

    private func scheduleDeadline(seconds: Double, generation: Int) {
        deadlineTask?.cancel()
        deadlineTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(seconds), clock: .continuous) } catch { return }
            guard let self, !self.closed, self.lease.generation == generation else { return }
            self.cancel(reason: seconds == 15 ? "timeout" : "expired")
        }
    }

    func route(receiptID: String, placeID: String, actionID: String) {
        guard !closed else { return }
        var reply = binding("route_result")
        reply["receiptID"] = receiptID; reply["placeID"] = placeID; reply["actionID"] = actionID
        guard !headless, mapWindow.isPresenting,
              validText(actionID, limit: 128), routeActions.count < 64,
              !routeActions.contains(actionID),
              let place = lease.route(receiptID: receiptID, placeID: placeID, now: now()),
              let origin = lease.output?.origin else {
            reply["status"] = "maps_handoff_failed"; sink(reply); return
        }
        routeActions.insert(actionID)
        let accepted = standardMapsHandoff(origin: origin, destination: place.coordinate, destinationName: place.name)
        reply["status"] = accepted ? "maps_handoff_accepted" : "maps_handoff_failed"
        sink(reply)
        // Bool establishes only OS handoff, not route calculation, ETA or actual navigation.
    }

    private func binding(_ event: String) -> [String: Any] {
        guard let input else { return ["event": event] }
        return ["event": event, "requestID": input.requestID, "sessionID": input.sessionID, "epoch": input.epoch]
    }

    func cancel(reason: String) {
        let previous = input
        provider.cancel(); lease.cancel(); deadlineTask?.cancel(); deadlineTask = nil
        routeActions.removeAll()
        if !headless, previous != nil { mapWindow.clear("Остановлено. Карточки и разрешение маршрута очищены.") }
        if previous != nil {
            var value = binding("cleared"); value["reason"] = reason; sink(value)
        }
        input = nil
    }

    func close() {
        closed = true
        cancel(reason: "closed")
        if !headless { mapWindow.close() }
    }
}
