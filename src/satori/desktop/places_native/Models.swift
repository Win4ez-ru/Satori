import Foundation

struct Coordinate: Codable, Equatable, Sendable {
    let latitude: Double
    let longitude: Double
    var valid: Bool { latitude.isFinite && longitude.isFinite && (-90...90).contains(latitude) && (-180...180).contains(longitude) }
}

struct SearchInput: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let requestID: String
    let sessionID: String
    let epoch: Int
    let city: String
    let district: String
    let query: String
    let radiusMeters: Int
    let explicitCenter: Coordinate?
    func valid() -> Bool {
        schemaVersion == 1 && !requestID.isEmpty && requestID.utf8.count <= 128
        && !sessionID.isEmpty && sessionID.utf8.count <= 128 && epoch >= 0
        && validText(city, limit: 120) && validText(district, limit: 180)
        && validText(query, limit: 240) && (100...5000).contains(radiusMeters)
        && (explicitCenter == nil || explicitCenter!.valid)
    }
}

func validText(_ value: String, limit: Int) -> Bool {
    !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    && value.utf8.count <= limit
    && !value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
}

struct AreaChoice: Codable, Equatable, Sendable {
    let choiceID: String
    let label: String
    let coordinate: Coordinate
}

struct PlaceRecord: Codable, Equatable, Sendable {
    let placeID: String
    let name: String
    let address: String
    let coordinate: Coordinate
    let distanceMeters: Int
    let cuisineStatus: String
    let hoursStatus: String
    let budgetStatus: String
}

struct SearchOutput: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let requestID: String
    let sessionID: String
    let epoch: Int
    let receiptID: String
    let status: String
    let source: String
    let observedAt: String
    let expiresAt: String
    let city: String
    let district: String
    let query: String
    let originLabel: String?
    let origin: Coordinate?
    let originConfirmed: Bool
    let places: [PlaceRecord]
    let areaChoices: [AreaChoice]
    let mapVisible: Bool
    let errorCode: String?
}

struct HelperCommand: Codable, Sendable {
    let operation: String
    let search: SearchInput?
    let receiptID: String?
    let placeID: String?
    let actionID: String?
}

func isoNow(_ date: Date = Date()) -> String { ISO8601DateFormatter().string(from: date) }
