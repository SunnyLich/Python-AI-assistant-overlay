import Foundation

/// Wire protocol for the Swift <-> Python brain seam. Mirrors
/// `wisp_brain/protocol.py`: one JSON object per line, UTF-8, `\n`-terminated.
///
///   request   {"id": Int, "method": String, "params": {...}}
///   response  {"id": Int, "ok": Bool, "result": <any> | "error": String}
///   event     {"event": String, "id": Int?, "data": <any>}
///
/// We use `JSONSerialization` with `[String: Any]` rather than `Codable` structs
/// because brain results are open-ended (arbitrary nested JSON) and a typed model
/// would just fight that. Helpers here keep the (de)framing in one place.
enum BrainProtocol {

    /// Encode a request dict into a single `\n`-terminated JSON line.
    static func encodeLine(_ object: [String: Any]) throws -> Data {
        var data = try JSONSerialization.data(withJSONObject: object, options: [])
        data.append(0x0A) // '\n'
        return data
    }

    /// Decode one protocol line. Returns nil for blank/garbage lines so a stray
    /// non-protocol write can't wedge the reader (matches the Python side).
    static func decodeLine(_ line: Data) -> [String: Any]? {
        guard !line.isEmpty else { return nil }
        guard let obj = try? JSONSerialization.jsonObject(with: line, options: []),
              let dict = obj as? [String: Any] else { return nil }
        return dict
    }

    static func request(id: Int, method: String, params: [String: Any]) -> [String: Any] {
        ["id": id, "method": method, "params": params]
    }
}

/// One item from a streaming call: zero or more `.event`s (id-tagged partials
/// like `reply.chunk`) followed by exactly one terminal `.result`.
enum BrainStreamItem {
    case event(name: String, data: [String: Any]?)
    case result([String: Any]?)
}

enum BrainError: Error, CustomStringConvertible {
    case notRunning
    case spawnFailed(String)
    case remote(String)        // brain returned {"ok": false, "error": ...}
    case malformedResponse

    var description: String {
        switch self {
        case .notRunning:           return "brain sidecar is not running"
        case .spawnFailed(let m):   return "failed to spawn brain sidecar: \(m)"
        case .remote(let m):        return "brain error: \(m)"
        case .malformedResponse:    return "malformed response from brain sidecar"
        }
    }
}
