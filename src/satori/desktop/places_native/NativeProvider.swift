import Foundation
import MapKit

@MainActor
protocol PlacesProviderPort: AnyObject {
    func resolveArea(_ input: SearchInput, completion: @escaping @MainActor ([AreaChoice]?, String?) -> Void)
    func search(_ input: SearchInput, origin: Coordinate, completion: @escaping @MainActor ([PlaceRecord]?, Bool, String?) -> Void)
    func cancel()
}

func coordinateOf(_ item: MKMapItem) -> Coordinate {
    if #available(macOS 26.0, *) {
        return Coordinate(latitude: item.location.coordinate.latitude, longitude: item.location.coordinate.longitude)
    }
    return Coordinate(latitude: item.placemark.coordinate.latitude, longitude: item.placemark.coordinate.longitude)
}

func addressOf(_ item: MKMapItem) -> String {
    if #available(macOS 26.0, *) {
        return boundedPlain(item.addressRepresentations?.fullAddress(includingRegion: true, singleLine: true) ?? "Адрес неизвестен", maxUTF8: 600)
    }
    return boundedPlain(item.placemark.title ?? "Адрес неизвестен", maxUTF8: 600)
}

func boundedPlain(_ text: String, maxUTF8: Int) -> String {
    var result = ""
    for scalar in text.unicodeScalars where !CharacterSet.controlCharacters.contains(scalar) {
        if result.utf8.count + scalar.utf8.count > maxUTF8 { break }
        result.unicodeScalars.append(scalar)
    }
    return result.isEmpty ? "Неизвестно" : result
}

func straightDistance(_ first: Coordinate, _ second: Coordinate) -> Double {
    let k = Double.pi / 180
    let value = pow(sin((second.latitude - first.latitude) * k / 2), 2)
        + cos(first.latitude * k) * cos(second.latitude * k)
        * pow(sin((second.longitude - first.longitude) * k / 2), 2)
    return 6_371_008.8 * 2 * asin(sqrt(min(1, max(0, value))))
}

@MainActor
func projectPlaces(_ items: [MKMapItem], input: SearchInput, origin: Coordinate) -> ([PlaceRecord], Bool) {
    var seen = Set<String>()
    let records = items.prefix(100).compactMap { item -> PlaceRecord? in
        let point = coordinateOf(item)
        guard point.valid else { return nil }
        let distance = straightDistance(origin, point)
        guard distance <= Double(input.radiusMeters) else { return nil }
        let name = boundedPlain(item.name ?? "Название неизвестно", maxUTF8: 400)
        let key = "\(point.latitude),\(point.longitude),\(name)"
        guard seen.insert(key).inserted else { return nil }
        return PlaceRecord(placeID: UUID().uuidString, name: name, address: addressOf(item), coordinate: point,
            distanceMeters: Int(distance.rounded()), cuisineStatus: "unknown", hoursStatus: "unknown", budgetStatus: "unknown")
    }.sorted { $0.distanceMeters < $1.distanceMeters }
    return (Array(records.prefix(20)), items.count >= 100 || records.count > 20)
}

@MainActor
final class NativePlacesProvider: PlacesProviderPort {
    private var active: MKLocalSearch?
    private var generation = 0
    private(set) var lastDiagnostic: (domain: String, code: Int)?

    func cancel() {
        generation += 1
        active?.cancel()
        active = nil
    }

    private func run(_ request: MKLocalSearch.Request, completion: @escaping @MainActor ([MKMapItem]?, String?) -> Void) {
        cancel()
        let expected = generation
        let search = MKLocalSearch(request: request)
        lastDiagnostic = nil
        active = search
        search.start { @Sendable [weak self] response, error in
            Task { @MainActor in
                guard let self, self.generation == expected else { return }
                self.active = nil
                guard error == nil, let response else {
                    let native = error as NSError?
                    let domain = native?.domain == MKErrorDomain ? "mapkit" : native?.domain == NSURLErrorDomain ? "url_loading" : "other"
                    self.lastDiagnostic = (domain, native?.code ?? 0)
                    completion(nil, "provider_unavailable"); return
                }
                completion(response.mapItems, nil)
            }
        }
    }

    func resolveArea(_ input: SearchInput, completion: @escaping @MainActor ([AreaChoice]?, String?) -> Void) {
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = "\(input.city), \(input.district)"
        // A deliberate world region; no current-device coordinate is acquired or supplied.
        request.region = MKCoordinateRegion(MKMapRect.world)
        request.resultTypes = [.address, .pointOfInterest]
        run(request) { items, error in
            guard let items else { completion(nil, error); return }
            var seen = Set<String>()
            let choices = items.prefix(100).compactMap { item -> AreaChoice? in
                let coordinate = coordinateOf(item)
                guard coordinate.valid else { return nil }
                let key = "\(coordinate.latitude),\(coordinate.longitude)"
                guard seen.insert(key).inserted else { return nil }
                let label = boundedPlain("\(item.name ?? input.district) · \(addressOf(item))", maxUTF8: 600)
                return AreaChoice(choiceID: UUID().uuidString, label: label, coordinate: coordinate)
            }
            completion(Array(choices.prefix(5)), nil)
        }
    }

    func search(_ input: SearchInput, origin: Coordinate, completion: @escaping @MainActor ([PlaceRecord]?, Bool, String?) -> Void) {
        let request = MKLocalSearch.Request()
        // City/district have already selected an explicit origin; keep POI text independent.
        request.naturalLanguageQuery = input.query
        request.region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: origin.latitude, longitude: origin.longitude),
            latitudinalMeters: Double(input.radiusMeters * 2), longitudinalMeters: Double(input.radiusMeters * 2)
        )
        request.resultTypes = .pointOfInterest
        if #available(macOS 15.0, *) { request.regionPriority = .required }
        run(request) { items, error in
            guard let items else { completion(nil, false, error); return }
            let (records, truncated) = projectPlaces(items, input: input, origin: origin)
            completion(records, truncated, nil)
        }
    }
}

@MainActor
func routeItems(origin: Coordinate, destination: Coordinate, destinationName: String) -> [MKMapItem] {
    func item(_ point: Coordinate, name: String) -> MKMapItem {
        let result: MKMapItem
        if #available(macOS 26.0, *) {
            result = MKMapItem(location: CLLocation(latitude: point.latitude, longitude: point.longitude), address: nil)
        } else {
            result = MKMapItem(placemark: MKPlacemark(coordinate: CLLocationCoordinate2D(latitude: point.latitude, longitude: point.longitude)))
        }
        result.name = name
        return result
    }
    return [item(origin, name: "Выбранная точка начала"), item(destination, name: destinationName)]
}

@MainActor
func standardMapsHandoff(origin: Coordinate, destination: Coordinate, destinationName: String) -> Bool {
    // Exactly two selected points; never forCurrentLocation() or destination-only fallback.
    return MKMapItem.openMaps(
        with: routeItems(origin: origin, destination: destination, destinationName: destinationName),
        launchOptions: [MKLaunchOptionsDirectionsModeKey: MKLaunchOptionsDirectionsModeWalking]
    )
}
