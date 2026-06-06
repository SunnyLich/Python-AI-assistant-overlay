import AVFoundation
import Foundation

@MainActor
final class AudioPlayer: NSObject, AVAudioPlayerDelegate {

    private var player: AVAudioPlayer?
    private var nextPlaybackID = 0
    private var activePlaybackID: Int?
    private var playbackIDs: [ObjectIdentifier: Int] = [:]
    private var amplitudeTimer: Timer?
    var onFinish: ((Int, Bool) -> Void)?
    var onAmplitude: ((Int, Double) -> Void)?

    func play(url: URL) throws -> Int {
        stop()
        let player = try AVAudioPlayer(contentsOf: url)
        nextPlaybackID += 1
        let playbackID = nextPlaybackID
        playbackIDs[ObjectIdentifier(player)] = playbackID
        activePlaybackID = playbackID
        player.delegate = self
        player.isMeteringEnabled = true
        player.prepareToPlay()
        player.play()
        self.player = player
        startAmplitudeMeter(for: playbackID)
        NSLog("[wisp] playing audio: %@", url.path)
        return playbackID
    }

    func stop() {
        stopAmplitudeMeter()
        if let player, player.isPlaying {
            player.stop()
        }
        if let player {
            playbackIDs.removeValue(forKey: ObjectIdentifier(player))
        }
        player = nil
        activePlaybackID = nil
    }

    static func normalizedAmplitude(averagePower: Float) -> Double {
        guard averagePower.isFinite else { return 0 }
        let clamped = min(0, max(-60, averagePower))
        return pow(10, Double(clamped) / 20)
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        NSLog("[wisp] audio playback finished: %@", String(describing: flag))
        let key = ObjectIdentifier(player)
        Task { @MainActor in
            self.finish(key, successfully: flag)
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        NSLog("[wisp] audio playback decode error: %@", String(describing: error))
        let key = ObjectIdentifier(player)
        Task { @MainActor in
            self.finish(key, successfully: false)
        }
    }

    private func finish(_ key: ObjectIdentifier, successfully flag: Bool) {
        guard let playbackID = playbackIDs.removeValue(forKey: key) else { return }
        if activePlaybackID == playbackID {
            stopAmplitudeMeter()
            player = nil
            activePlaybackID = nil
            onFinish?(playbackID, flag)
        }
    }

    private func startAmplitudeMeter(for playbackID: Int) {
        stopAmplitudeMeter()
        onAmplitude?(playbackID, 0)
        amplitudeTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 24.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                guard self.activePlaybackID == playbackID,
                      let player = self.player,
                      player.isPlaying else {
                    self.stopAmplitudeMeter()
                    return
                }
                player.updateMeters()
                let amplitude = Self.normalizedAmplitude(averagePower: player.averagePower(forChannel: 0))
                self.onAmplitude?(playbackID, amplitude)
            }
        }
    }

    private func stopAmplitudeMeter() {
        amplitudeTimer?.invalidate()
        amplitudeTimer = nil
    }
}
