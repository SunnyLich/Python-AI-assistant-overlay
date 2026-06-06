import XCTest
@testable import Wisp

final class AudioPlayerTests: XCTestCase {

    func testNormalizedAmplitudeClampsSilenceAndPeak() {
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: -120), 0.001, accuracy: 0.000_001)
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: 12), 1.0, accuracy: 0.000_001)
    }

    func testNormalizedAmplitudeMapsDecibelsToLinearPower() {
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: -20), 0.1, accuracy: 0.000_001)
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: -6), 0.501187, accuracy: 0.000_001)
    }

    func testNormalizedAmplitudeRejectsNonFiniteValues() {
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: .nan), 0)
        XCTAssertEqual(AudioPlayer.normalizedAmplitude(averagePower: .infinity), 0)
    }
}
