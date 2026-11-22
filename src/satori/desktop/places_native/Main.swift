import AppKit
import Darwin
import Foundation

/// One queued main-actor input; reject bursts before creating an unbounded Task backlog.
final class IngressGate: @unchecked Sendable {
    private let lock = NSLock()
    private var pending = false
    private var failed = false
    func enter() -> Bool { lock.lock(); defer { lock.unlock() }; guard !pending, !failed else { return false }; pending = true; return true }
    func leave() { lock.lock(); pending = false; lock.unlock() }
    func failOnce() -> Bool { lock.lock(); defer { lock.unlock() }; guard !failed else { return false }; failed = true; return true }
}

/// Accessed only on the single owned stdin dispatch queue.
final class InputLines: @unchecked Sendable {
    var lines = BoundedLineBuffer()
}

@MainActor
final class HelperApp: NSObject, NSApplicationDelegate {
    private let publicSmoke: Bool
    private var coordinator: PlacesCoordinator!
    private var source: DispatchSourceRead?
    private var lifetime: Task<Void, Never>?
    private let clockOrigin = ContinuousClock().now
    private let ingress = IngressGate()
    private var commandCount = 0

    init(publicSmoke: Bool = false) { self.publicSmoke = publicSmoke; super.init() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if publicSmoke {
            let provider = NativePlacesProvider()
            coordinator = PlacesCoordinator(provider: provider, headless: true, now: { [weak self] in self?.elapsed() ?? .infinity }) { [weak self] event in
                if let result = event["result"] as? [String: Any] {
                    let records = result["places"] as? [[String: Any]] ?? []
                    let bytes = (try? JSONSerialization.data(withJSONObject: records).count) ?? 0
                    writeEnvelope(["status": result["status"] as? String ?? "unavailable", "typed_count": records.count, "typed_bytes": bytes, "all_hours_unknown": records.allSatisfy { $0["hoursStatus"] as? String == "unknown" }, "provider_error_domain": provider.lastDiagnostic?.domain ?? "none", "provider_error_code": provider.lastDiagnostic?.code ?? 0, "public_query_only": true, "map_window_created": false, "route_opened": false])
                    self?.finish()
                } else if event["event"] as? String == "cleared", event["reason"] as? String == "timeout" {
                    writeEnvelope(["status": "TIMEOUT", "public_query_only": true, "map_window_created": false, "route_opened": false]); self?.finish()
                }
            }
            coordinator.search(SearchInput(schemaVersion: 1, requestID: "public-app-smoke", sessionID: "public-app-smoke", epoch: 0, city: "Санкт-Петербург", district: "Дворцовая площадь", query: "китайский ресторан", radiusMeters: 2000, explicitCenter: Coordinate(latitude: 59.9398, longitude: 30.3146)))
            return
        }
        coordinator = PlacesCoordinator(provider: NativePlacesProvider(), now: { [weak self] in self?.elapsed() ?? .infinity }, sink: writeEnvelope)
        let gate = ingress
        let stream = DispatchSource.makeReadSource(fileDescriptor: STDIN_FILENO, queue: DispatchQueue(label: "satori.places.owned-input"))
        source = stream
        let lines = InputLines()
        stream.setEventHandler { @Sendable [weak self] in
            var bytes = [UInt8](repeating: 0, count: 4096)
            let count = Darwin.read(STDIN_FILENO, &bytes, bytes.count)
            if count <= 0 {
                if gate.failOnce() { Task { @MainActor in self?.finish() } }
                return
            }
            guard let frames = lines.lines.append(Data(bytes.prefix(count))) else {
                if gate.failOnce() { Task { @MainActor in self?.protocolFailure() } }
                return
            }
            for frame in frames {
                guard gate.enter() else {
                    if gate.failOnce() { Task { @MainActor in self?.protocolFailure() } }
                    return
                }
                Task { @MainActor in
                    defer { gate.leave() }
                    self?.command(frame)
                }
            }
        }
        stream.resume()
        lifetime = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(900), clock: .continuous) } catch { return }
            self?.finish()
        }
    }

    private func elapsed() -> Double {
        let value = clockOrigin.duration(to: ContinuousClock().now).components
        return Double(value.seconds) + Double(value.attoseconds) / 1e18
    }

    private func command(_ data: Data) {
        commandCount += 1
        guard commandCount <= 64 else { protocolFailure(); return }
        guard let command = parseCommand(data) else { protocolFailure(); return }
        switch command.operation {
        case "search": if let search = command.search { coordinator.search(search) }
        case "route":
            if let receipt = command.receiptID, let place = command.placeID, let action = command.actionID {
                coordinator.route(receiptID: receipt, placeID: place, actionID: action)
            }
        case "cancel": coordinator.cancel(reason: "cancelled")
        case "close": finish()
        default: protocolFailure()
        }
    }

    private func protocolFailure() { writeEnvelope(["event": "protocol_error", "code": "input_invalid"]); finish() }
    private func finish() {
        source?.cancel(); source = nil; lifetime?.cancel(); lifetime = nil
        coordinator?.close()
        NSApplication.shared.terminate(nil)
    }
    func applicationWillTerminate(_ notification: Notification) {
        source?.cancel(); lifetime?.cancel(); coordinator?.close()
    }
}

@MainActor
func runOwnedHelper(publicSmoke: Bool = false) {
    let app = NSApplication.shared
    let delegate = HelperApp(publicSmoke: publicSmoke); app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}

@MainActor
func publicSmoke(areaOnly: Bool) async -> Int32 {
    let provider = NativePlacesProvider()
    let input = SearchInput(schemaVersion: 1, requestID: "public-smoke", sessionID: "public-smoke", epoch: 0, city: "Санкт-Петербург", district: "Дворцовая площадь", query: "китайский ресторан", radiusMeters: 2000, explicitCenter: Coordinate(latitude: 59.9398, longitude: 30.3146))
    return await withCheckedContinuation { continuation in
        var finished = false
        let timeout = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(15), clock: .continuous) } catch { return }
            guard !finished else { return }; finished = true; provider.cancel()
            writeEnvelope(["status": "TIMEOUT", "public_query_only": true]); continuation.resume(returning: 2)
        }
        if areaOnly {
            provider.resolveArea(input) { choices, error in
                guard !finished else { return }; finished = true; timeout.cancel()
                writeEnvelope(["status": error == nil ? "COMPLETED" : "UNAVAILABLE", "public_query_only": true, "area_choice_count": choices?.count ?? 0, "origin_automatically_selected": false, "route_opened": false, "native_error_domain": provider.lastDiagnostic?.domain ?? "none", "native_error_code": provider.lastDiagnostic?.code ?? 0])
                continuation.resume(returning: error == nil ? 0 : 3)
            }
        } else {
            provider.search(input, origin: input.explicitCenter!) { records, partial, error in
                guard !finished else { return }; finished = true; timeout.cancel()
                let data = (try? JSONEncoder().encode(records ?? [])) ?? Data()
                writeEnvelope(["status": error == nil ? "COMPLETED" : "UNAVAILABLE", "public_query_only": true, "typed_count": records?.count ?? 0, "typed_bytes": data.count, "all_hours_unknown": records?.allSatisfy { $0.hoursStatus == "unknown" } ?? false, "partial": partial, "map_gui": "NOT_RUN", "route_opened": false, "native_error_domain": provider.lastDiagnostic?.domain ?? "none", "native_error_code": provider.lastDiagnostic?.code ?? 0])
                continuation.resume(returning: error == nil ? 0 : 3)
            }
        }
    }
}

@main
struct PlacesHelperMain {
    @MainActor static func main() {
        signal(SIGPIPE, SIG_IGN)
        switch Array(CommandLine.arguments.dropFirst()) {
        case ["--public-smoke"]:
            Task { @MainActor in exit(await publicSmoke(areaOnly: false)) }
            dispatchMain()
        case ["--public-area-smoke"]:
            Task { @MainActor in exit(await publicSmoke(areaOnly: true)) }
            dispatchMain()
        case ["--self-test"]: runSelfTests()
        case ["--stdio"]:
            runOwnedHelper()
        case ["--app-public-smoke"]:
            runOwnedHelper(publicSmoke: true)
        default: writeEnvelope(["status": "NOT_STARTED", "network_dispatched": false, "gps_used": false])
        }
    }
}
