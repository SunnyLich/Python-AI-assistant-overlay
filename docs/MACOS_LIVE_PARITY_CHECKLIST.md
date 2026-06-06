# Native macOS Live Parity Checklist

The automated native quick test covers Python brain/config contracts and Swift
package tests. These checks still need a real interactive macOS app session
because they depend on AppKit windows, TCC permissions, system keychain prompts,
browser auth, audio devices, or LaunchServices.

## Must Check After Native Tests Pass

- [ ] Overlay appears with readable idle/listening/thinking/speaking states.
- [ ] Overlay right-click opens the native context menu.
- [ ] WASD/caller intent overlay is readable in light mode and dark mode.
- [ ] Custom prompt field inside the intent overlay is readable while typing.
- [ ] Response bubble reply/listening/notice text is readable.
- [ ] Chat window is readable in light mode and dark mode.
- [ ] Caller hotkeys open the expected caller/intent flow.
- [ ] Snip Screen Region captures a selected region and sends the query.
- [ ] Voice query records, transcribes, sends, and stops cleanly.
- [ ] Speak Last Response plays audio and pulses the overlay.
- [ ] Memory window can refresh, add, edit, delete, and search facts.
- [ ] Plugin Manager refreshes and can run a configured action.
- [ ] Agent Task and Agent History flows open and can start/read a run.
- [ ] Settings saves model, caller, voice, memory, and UI changes.
- [ ] Settings > Keys can refresh, save, and clear API-key status.
- [ ] Settings > Auth can refresh provider status.
- [ ] ChatGPT browser sign-in completes or reports a clear actionable error.
- [ ] GitHub device sign-in opens the verification URL and records completion.
- [ ] Copilot token save/test/clear works when a token is available.
- [ ] Reset All clears credentials/.env only after confirmation and reloads UI.
- [ ] Permissions panel reflects Accessibility, Screen Recording, and Microphone.
- [ ] Launch at Login toggle updates System Settings state.
- [ ] `--open` launch writes `native-app-launch.log`.

## Package/Release Gate

- [ ] Embedded runtime package passes import probe.
- [ ] Signed app launches with `WISP_VALIDATE_APP_LAUNCH=1`.
- [ ] Developer ID notarization and stapling complete.
- [ ] Signed/notarized app repeats the permission, auth, voice, snip, and settings checks.
