import Foundation
import Darwin

let maximumRequestBytes = 8192
let maximumOutputBytes = 256 * 1024

func parseCommand(_ data: Data) -> HelperCommand? {
    guard !data.isEmpty, data.count <= maximumRequestBytes,
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let operation = object["operation"] as? String else { return nil }
    let allowed: Set<String>
    switch operation {
    case "search":
        allowed = ["operation", "search"]
        guard let search = object["search"] as? [String: Any],
              Set(search.keys).isSubset(of: ["schemaVersion", "requestID", "sessionID", "epoch", "city", "district", "query", "radiusMeters", "explicitCenter"]) else { return nil }
        if let point = search["explicitCenter"] as? [String: Any], Set(point.keys) != ["latitude", "longitude"] { return nil }
    case "route": allowed = ["operation", "receiptID", "placeID", "actionID"]
    case "cancel", "close": allowed = ["operation"]
    default: return nil
    }
    guard Set(object.keys) == allowed,
          let command = try? JSONDecoder().decode(HelperCommand.self, from: data) else { return nil }
    if operation == "search", command.search?.valid() != true { return nil }
    if operation == "route" {
        guard let receipt = command.receiptID, let place = command.placeID, let action = command.actionID,
              validText(receipt, limit: 128), validText(place, limit: 128), validText(action, limit: 128) else { return nil }
    }
    return command
}

struct BoundedLineBuffer {
    private(set) var data = Data()
    mutating func append(_ bytes: Data) -> [Data]? {
        var frames: [Data] = []
        for byte in bytes {
            if byte == 10 {
                guard !data.isEmpty else { return nil }
                frames.append(data); data.removeAll(keepingCapacity: true)
            } else {
                guard data.count < maximumRequestBytes else { data.removeAll(); return nil }
                data.append(byte)
            }
        }
        return frames
    }
}

@MainActor
func writeEnvelope(_ value: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(value),
          let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
          data.count <= maximumOutputBytes else {
        writeBytes(Data("{\"event\":\"protocol_error\",\"code\":\"output_limit\"}\n".utf8))
        return
    }
    writeBytes(data + Data([10]))
}

func writeBytes(_ data: Data) {
    data.withUnsafeBytes { buffer in
        guard let base = buffer.baseAddress else { return }
        var offset = 0
        while offset < data.count {
            let written = Darwin.write(STDOUT_FILENO, base.advanced(by: offset), data.count - offset)
            if written > 0 { offset += written }
            else if errno != EINTR { exit(3) }
        }
    }
}
