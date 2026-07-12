# OpenSpan Clipboard — one-time setup

*Companion to CLIPBOARD_DESIGN.md. Your values: PC = `<PC-LAN-IP>` (your PC's
address on the LAN — `ipconfig`), port = `9966`, and the token is the
`clipboard_token` value in
`D:\OpenSpan\openspan_settings.json` (minted at first app launch; that file
is gitignored — the token must NEVER be committed or published; the first
minted one was burned during review and has been rotated). To rotate at any
time: delete the `clipboard_token` line, relaunch, update both shortcuts.
Recommended: give the PC a DHCP reservation in the router so the IP never
moves (mDNS `.local` is flakier than a pinned IP on both ends).*

## 1. PC — firewall rule (once, elevated PowerShell/cmd)

```
netsh advfirewall firewall add rule name="OpenSpan clipboard" dir=in action=allow protocol=TCP localport=9966 remoteip=LocalSubnet
```

(Skipping this and clicking through the Windows Security Alert also works
since you're admin — but the netsh route is deliberate and LAN-scoped.)

## 2. iPad — Shortcut "Paste from PC"  (Windows → iPad)

Shortcuts app → + → add two actions:

1. **Get Contents of URL**
   - URL: `http://<PC-LAN-IP>:9966/clip`
   - Method: **GET**
   - Headers: `X-OpenSpan-Token` = `<clipboard_token from openspan_settings.json>`
2. **Copy to Clipboard** (input: *Contents of URL*)

Name it `Paste from PC`. Run it once by hand:
- Accept the **Local Network** prompt (one-time, lands on Shortcuts).
- Choose **Always Allow** for contacting `<PC-LAN-IP>`.

## 3. iPad — Shortcut "Copy to PC"  (iPad → Windows)

1. **Get Clipboard**
2. **Get Contents of URL**
   - URL: `http://<PC-LAN-IP>:9966/clip`
   - Method: **POST**
   - Headers: `X-OpenSpan-Token` = `<clipboard_token from openspan_settings.json>`
   - Request Body: **JSON**, one field: `text` = *Clipboard* (the variable)

Name it `Copy to PC`. Run it once by hand:
- Answer the **Allow Paste** prompt, then go to
  **Settings → Shortcuts → Paste from Other Apps → Allow**
  (the menu entry only exists after that first prompt; without this the
  paste prompt recurs on every run — it has no "always" button).
- Choose **Always Allow** for sending to `<PC-LAN-IP>`.

Also flip once: **Settings → Shortcuts → Advanced → Allow Sharing Large
Amounts of Data** (big clipboard payloads).

## 4. iPad — the FKA key bindings (the trial you chose)

**Settings → Accessibility → Keyboards → Full Keyboard Access** → ON →
**Commands** → scroll to the **Shortcuts** section:

- `Paste from PC` → press **Ctrl+Opt+G**
- `Copy to PC` → press **Ctrl+Opt+H**

(The portal sends exactly these chords. If FKA's focus ring / Tab-Space
quirks annoy you, turn FKA off — the shortcuts still work from a widget or
home-screen icon; nothing else breaks.)

## 5. Daily use — SEAMLESS: plain Ctrl+C / Ctrl+V, no special combos

The portal keeps the two clipboards converged by firing the FKA chords for
you at the right moments:

| What you do | What happens |
|---|---|
| Cross into the iPad | if you copied anything new on Windows, it's handed to the iPad automatically (~1 s, brief banner) |
| **Ctrl+C / Ctrl+X** on the iPad | normal copy/cut (Cmd+C via the keymap) — **and** it lands on the Windows clipboard a second later |
| **Ctrl+V** — anywhere | plain instant paste; always the newest copy from either machine |

Backups (still there, rarely needed): **Ctrl+Alt+Shift+V** = force
Windows→iPad, **Ctrl+Alt+Shift+C** = force iPad→Windows, Ctrl+Alt+V = the
old typing fallback.

Known edges:
- Copies made by **touch** (long-press → Copy) on the iPad don't auto-push —
  tap Ctrl+C after selecting instead, or hit Ctrl+Alt+Shift+C.
- Each sync flashes the Shortcuts banner (~3 s, auto-dismisses).
- A keystroke typed in the ~0.1 s protection window right after a copy can
  be swallowed — if you ever actually notice this, say so and it gets the
  replay treatment.

Needs the **app restarted once** (relay + portal changes) and the iPad
steps above.
