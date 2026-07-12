# OpenSpan — Two-Way Clipboard Design (investigation, 2026-07-10)

*Status: DESIGN ONLY — nothing implemented. Facts below were verified by a
6-agent research pass with sources; the two load-bearing claims were
independently adversarially cross-checked. Applies to an iPad 7th gen,
which tops out at iPadOS 18.x (all cited features exist there).*

---

## 1. The platform truths that shape everything

- **HID cannot read anything back.** OpenSpan's keyboard is one-way input
  into the iPad; the only host→peripheral channel is the 1-byte LED output
  report. No Bluetooth profile exposes the iOS clipboard. So iPad→Windows
  **must** ride the LAN.
- **iOS forbids background clipboard reads, for everyone.** Every clipboard
  tool on iOS is either manual-foreground or a keyboard/share extension.
  There is no daemon-style sync to buy or clone — the bespoke design below
  hits the platform ceiling, not our ceiling.
- **Our unfair advantage:** the PC *is* the iPad's hardware keyboard. iPadOS
  can bind a hardware-key combo to run a Shortcuts shortcut system-wide —
  so the PC can remotely trigger the iPad-side clipboard work. No existing
  tool (KDE Connect, LocalSend, PairDrop) can do that; they all need a hand
  on the iPad for every sync.

**Correction from the research (my initial assumption was wrong):** the
keyboard binding is NOT in the Shortcuts app (that's Mac-only). On iPadOS
it lives in **Settings → Accessibility → Keyboards → Full Keyboard Access
(FKA) → Commands**, where every shortcut in the library can be given a key
combo (since iPadOS 13.4; system-wide from any app). The tradeoff: FKA must
stay ON, and it has side effects — a visible focus ring, and Tab/Space/
arrows gain navigation meanings outside text fields; users report occasional
key-interception quirks. **This is the one genuine "is it worth it" decision
in the whole design** (see §5). Without FKA, everything still works — the
trigger is a tap on an iPad widget instead of a keystroke from the desk.

## 2. Architecture

```
Windows clipboard  ⇄  ClipboardServer (in openspan.py, stdlib http.server,
                       LAN :9966, token header, GET /clip + POST /clip)
                            ⇅  Wi-Fi (one-time firewall rule)
iPad Shortcuts app:  "Paste from PC"  = Get Contents of URL → Copy to Clipboard
                     "Copy to PC"     = Get Clipboard → POST to the relay
Triggers: FKA key combos sent BY THE PC through the existing HID keyboard
          (or manual: widget / home-screen icon / share sheet)
```

### Flow A — Windows → iPad (real clipboard, full Unicode, any size)
1. The user (controlling the iPad through the portal) hits **Ctrl+Alt+Shift+V**.
2. The portal sends the FKA-bound chord (e.g. **Ctrl+Opt+G**) as HID.
3. iPad runs "Paste from PC": GET `http://<PC>:9966/clip` → Copy to Clipboard.
4. Optional: portal follows with Cmd+V after ~1.5 s so it also pastes in place.
Zero taps. The existing **Ctrl+Alt+V typing fallback stays** (needs no setup,
works today, ASCII-ish only).

### Flow B — iPad → Windows
1. Copy on the iPad as normal (Cmd+C from our keyboard works already).
2. The user hits **Ctrl+Alt+Shift+C** → portal sends **Ctrl+Opt+H**.
3. iPad runs "Copy to PC": Get Clipboard → POST to the relay.
4. The app sets the Windows clipboard and logs "clipboard received from iPad
   (N chars)" in the console.
Zero taps after setup (see the one-time settings below).

## 3. One-time setup (verified specifics)

| Step | Where | Notes |
|---|---|---|
| Firewall rule for :9966 | PC, elevated once | `netsh advfirewall firewall add rule name="OpenSpan clipboard" dir=in action=allow protocol=TCP localport=9966 remoteip=LocalSubnet` — same one-time-UAC pattern as the boot task. (Unelevated listeners otherwise hit an allow-prompt that non-admins can't approve; a wrong click creates sticky block rules.) |
| Two Shortcuts | iPad Shortcuts app | ~4 actions each; exact recipes ship with the implementation. Use the PC's LAN IP with a DHCP reservation (mDNS `.local` works in principle but has two documented flakiness surfaces — Windows responder + Shortcuts `.local` resolution). |
| Local Network permission | iPad, first run | One-time prompt, lands on the Shortcuts app; run each shortcut once by hand in the foreground to receive it. |
| Per-shortcut "Always Allow" for the host | iPad, first run | One-time per shortcut (Allow Once / **Always Allow**). |
| **Paste from Other Apps = Allow** | Settings → Shortcuts | Required for zero-tap Flow B: the iOS 16+ "Allow Paste" prompt has NO always-allow button and recurs by default; this per-app setting (16.1+) silences it permanently. The menu entry only appears AFTER the first prompt — run once, answer, then flip it. |
| Allow Sharing Large Amounts of Data | Settings → Shortcuts → Advanced | For big clipboard payloads. |
| (Optional) FKA + Commands bindings | Settings → Accessibility | Bind "Paste from PC"/"Copy to PC" to **Ctrl+Opt chords** (community-proven collision-free; avoid Cmd chords — system/app-reserved). |

## 4. Security

- Shared token in a request header, generated once, stored in
  `openspan_settings.json` and pasted into both shortcuts. Without it any
  LAN device could read/write the Windows clipboard (which carries passwords).
- Firewall rule scoped to `LocalSubnet`; server runs only while the app runs;
  plain HTTP on the home LAN (acceptable with token; noted honestly).

## 5. User decisions

1. **FKA on or off?** On = one-keystroke sync both directions, but the
   accessibility mode's focus ring + key-behavior quirks apply to ALL
   OpenSpan typing on the iPad. Off = identical functionality, triggered by
   tapping a widget/icon on the iPad. Recommendation: build Phase 1
   trigger-agnostic, try FKA for a day, keep whichever feels right.
2. Port (default :9966) and combo choices.
3. Phase 2 (images) — later, if ever; text first.

## 6. Effort

Phase 1 (text, both directions): ~150 lines total — `ClipboardServer` thread
(stdlib `http.server`) + `set_clipboard_text` (ctypes, mirror of the existing
getter) in openspan.py, two portal hotkeys, settings entries, harness tests
(the relay is fully testable locally with HTTP calls — no Bluetooth needed).
One session. The iPad-side shortcuts are ~2 minutes each with the recipes.

## 7. Alternatives considered (all free/open-source, all inferior here)

- **KDE Connect** (iOS 0.5.5 + Windows client): same manual-foreground
  interaction class, historical iOS clipboard-overwrite bug, rough Windows
  client — and the PC can't trigger it remotely. Fine as a bonus file-transfer
  channel, not as the clipboard mechanism.
- **LocalSend / PairDrop**: share tools, foreground-manual by construction.
- **CrossPaste / Phone Link / Universal Clipboard**: subscription-gated iOS
  app / iPhone-only / Apple-only respectively.

## 8. Open items (unverifiable without the device)

- FKA-vs-in-app precedence for identical combos (undocumented; use odd
  Ctrl+Opt chords to sidestep).
- Whether FKA's typing quirks are tolerable in daily use — the user's call
  after a live trial.
- The transient ~3 s Shortcuts banner per run (cosmetic, auto-dismisses).
