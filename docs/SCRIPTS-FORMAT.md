# EsotericOS scripts

A native, scoped keyboard scripting format. You write plain text files; the app
reads them and binds chords that only fire in the situation you named — on this
machine, on that monitor, in that application.

There is no AutoHotkey here and nothing is shelled out to. The engine is
`win/script_engine.py` (parser, runner, consumer, lifetime) over
`win/script_scope.py` (the pure resolution model). Both are stdlib + ctypes.

---

## Where scripts live

Files end in `.eos` and sit in the scripts directory: `ConfigStore`'s data
directory plus `scripts`, which is `<product root>\data\scripts` — the same
root that holds `config\esotericos_config.json`, and beside the exe on a frozen
build. It is overridable with the `directory` setting of the `native-scripts`
feature.

The app creates that directory on first run if it is absent and drops one
commented example, `10-example.eos`, into it. Delete the example and it does
not come back: an existing directory is never re-seeded.

Every `*.eos` in that directory is loaded, in filename order. Filename order is
the declaration order used to break exact ties, so `10-desk.eos` is consulted
before `20-code.eos`.

### The Scripts surface

The **Scripts** entry in the app's right-side dock is the whole UI. It shows
the directory, every file found with its scopes and binding count, every parse
problem with file, line, column and reason, and what each bound chord would
resolve to against the window that has the focus right now. It has two
controls: **Reload**, which re-reads the directory, and the switch that turns
native scripts on and off.

There is no editor and there will not be one. You write the files; the app
reads them.

The feature ships **off** — `native-scripts` declares `default_enabled=False` —
so a file in the scripts directory binds nothing until the switch is thrown
once. The switch writes the feature flag, so the choice survives a restart, and
the engine re-arms itself at the next launch without asking again.

---

## Grammar

```ebnf
script        = { line } ;
line          = blank | comment | directive | binding ;

comment       = "#" , { any-character } ;          (* to end of line *)
directive     = scope-header | priority-line ;

scope-header  = "scope" , scope-terms ;
scope-terms   = "os" | { screen-term | window-term } ;   (* at least one *)
screen-term   = "screen" , screen-key ;
screen-key    = "primary" | 16 * hex-digit ;
window-term   = "window" , criterion , { criterion } ;
criterion     = ( "process" , "=" , value )
              | ( "class"   , "=" , value )
              | ( "title"   , "~" , value ) ;
value         = bare-word | quoted-string ;
quoted-string = '"' , { character | '\"' | '\\' } , '"' ;

priority-line = "priority" , integer ;

binding       = chord , " -> " , action , { " ; " , action } ;
action        = "pass"
              | "send"    , key-sequence
              | "window"  , window-verb , [ argument ]
              | "catalog" , record-id ;
window-verb   = "tile" | "refine" | "restore" | "center"
              | "apply-rules" | "save-preset" | "restore-preset" ;
```

Whitespace and indentation are cosmetic. Indent under a `scope` header if you
like; the parser does not care.

Two spelling rules exist so a chord can contain punctuation:

* **`->` needs a space on each side.** Otherwise `Ctrl+- -> send x` would be
  ambiguous.
* **`;` between actions needs a space on each side.** Otherwise `send Ctrl+;`
  could not be written.

Both are reported by name if you get them wrong.

---

## Scopes

Three levels, narrowest first:

| Level | Written | Matches when |
|---|---|---|
| **window** | `scope window process=code` | the foreground window matches every criterion |
| **screen** | `scope screen primary` | the foreground window is on that display |
| **os** | `scope os` | always |

A scope is a **conjunction**, so nesting is just naming more than one thing:

```
scope screen 9f2c4a1b7e0d3355 window process=code
```

is a *window*-level scope — the narrowest thing it names — that requires both
the monitor and the process. That is what "on the left screen, in VS Code"
means, and it is one scope, not two.

`scope os` stands alone; it is the widest scope and may not be combined.

### Screen keys

`primary` is the one reserved name. Every other screen is named by its
`MonitorIdentity.stable_key`: sixteen hex characters derived from EDID (with
serial where the display gives one), which survives a reboot, a cable swap and
a rearrangement. Anything else is rejected at parse time. Run
`python win\monitor_identity.py` to print the keys for the displays attached
now.

### Window criteria

Matching is `window_rules.match` — the same matcher the window-placement rules
already use, not a second one:

* `process=code` — executable name, case-insensitive, a trailing `.exe` is
  stripped, so `process=code`, `process=Code` and `process=CODE.EXE` are the
  same criterion.
* `class=Chrome_WidgetWin_1` — window class, whole match, case-insensitive.
* `title~Visual Studio` — title **substring**, case-insensitive. The `~` is
  there to remind you it is a substring; `title=` is refused.

Use quotes for a value with spaces: `title~"Visual Studio Code"`.

---

## Resolution: most specific wins

For a chord, in this order:

1. **Level** — window beats screen beats os.
2. **`priority`** — higher first, *within* a level.
3. **Specificity** — the number of criteria named, screen included.
4. **Declaration order** — file order, then line order.

Steps 2–4 are exactly how `window_rules` already resolves competing rules.
Step 1 is the only thing scripts add.

Level is deliberately outside priority: a `priority 100` on an `os` scope does
**not** beat a plain `window` scope. Narrow always wins, and `pass` is how you
say otherwise.

Resolution is a pure function of (chord, window facts, screen key, binding set)
— `script_scope.resolve`. It touches no window, no monitor and no file, which
is why the whole nesting model is testable without pressing a key.

### Falling through with `pass`

```
scope window process=code
  Ctrl+Alt+H -> pass          # in VS Code, let the wider binding have it
```

`pass` yields to the next-most-specific match. If every match passes, the chord
reaches Windows untouched. Because the engine sits at consumer priority 40 and
the shipped tiling host sits at 50, a `pass` also hands the chord back to the
built-in shortcuts.

`pass` stands alone. A binding either falls through or acts; it cannot do both,
because the hook must answer swallow-or-not in one synchronous decision.

### What scripts can never claim

`PROTECTED_CHORDS` — Ctrl+Alt+Delete, Win+L, Alt+F4, Win+Ctrl+Shift+B and F12 —
always pass to Windows. Binding one is a parse error, and the resolver and the
consumer each refuse it again independently.

Any chord no binding mentions is passed on untouched, without even looking at
the foreground window.

---

## Actions

### `send <keys>`

Injects a chord or a chord sequence with `SendInput`.

```
Ctrl+Alt+S -> send Ctrl+Shift+S
Ctrl+Alt+D -> send Ctrl+K, then Ctrl+D
```

Two things happen for you. The injected keys carry EsotericOS's own
`dwExtraInfo` signature, which the router answers with pass-through before any
consumer is asked — so a `send` can never re-trigger the binding that sent it.
And the modifiers you are physically holding are neutralised for the duration
and restored afterwards, minimally: pressing `Ctrl+Alt+S` bound to
`send Ctrl+S` releases Alt, taps Ctrl+S, and puts Alt back. Ctrl is never
released and re-tapped, so a held modifier does not flicker.

A key with no virtual-key code is refused at parse time, not at press time.

### `window <verb> [argument]`

Calls a `hotkey_host.WindowActions` verb on the focused window.

| Written | Does |
|---|---|
| `window tile <zone>` | `left`, `right`, `top`, `bottom`, `top-left`, `top-right`, `bottom-left`, `bottom-right` (and the `-half` spellings) |
| `window refine <direction>` | `left`, `right`, `up`, `down` |
| `window restore` | back to the pre-tile bounds |
| `window center` | centre on the current work area |
| `window apply-rules` | run the window placement rules now |
| `window save-preset <name>` | capture the desktop layout |
| `window restore-preset <name>` | put it back |

Zones and directions are resolved when the file is parsed, so a typo is caught
in the editor, not in the hook.

### `catalog <record-id>`

Opens one control-catalog entry through the existing broker
(`control_center_client.request_activation`). The id is the catalog's, and the
broker validates it — the script cannot pass a target, a route, or arguments.
If the catalog or broker is unavailable the step reports that and the rest of
the binding still runs.

### `pass`

Described above.

---

## When a script is wrong

Nothing raises and nothing crashes.

* A line that will not parse is **reported and dropped**. The rest of the file
  still loads. The report is `file.eos:LINE:COLUMN: message` — the same
  recovery discipline `window_rules.parse_rules` uses per rule.
* A file that cannot be read or decoded is **disabled with a reason**. Every
  other file still loads.
* A verb that fails at run time returns a reported outcome. It never escapes
  into the keyboard hook.

```
typo.eos:2:17: unknown verb 'fly'; use send, window, catalog or pass
desk.eos:8:15: 'sideways' is not a tiling zone
desk.eos:3:1: Alt+F4 is a protected chord and always reaches Windows
```

---

## Limits, stated plainly

* **Keyboard only.** There is no shared mouse router in this product —
  `screen_zoom` and the portal each own a `WH_MOUSE_LL` hook independently — so
  v1 binds no mouse event and installs no mouse hook.
* **Single-chord triggers.** `Ctrl+K, then Ctrl+D` is legal as a `send`
  payload but not as a trigger; there is no chord-sequence state machine.
  Multi-chord triggers would need one, and it does not exist.
* **No variables, loops, or conditions.** Scope *is* the condition. If you want
  branching, write two scopes.
* **No new verbs.** The verb set is exactly what EsotericOS can already do.

---

## A worked example

```
# 10-desk.eos

# Everywhere, unless something narrower says otherwise.
scope os
  Ctrl+Alt+H -> window tile left-half
  Ctrl+Alt+L -> window tile right-half
  Ctrl+Alt+0 -> window restore

# On the laptop panel the halves are too narrow; centre instead.
scope screen primary
  priority 5
  Ctrl+Alt+H -> window center

# On the big display, in VS Code, with the project open: two-key save-all,
# and hand the tiling chord back to whatever is wider.
scope screen 9f2c4a1b7e0d3355 window process=code title~"esoteric-path"
  Ctrl+Alt+H -> pass
  Ctrl+Alt+S -> send Ctrl+K, then Ctrl+S ; window apply-rules

# Notepad gets one thing only.
scope window class=Notepad
  Ctrl+Alt+D -> catalog ms-settings-display
```

Press **Ctrl+Alt+H** with VS Code focused on the big display, project open:

1. Three bindings claim the chord: the window one, the screen one, the os one.
2. Window level wins — but it is `pass`, so it stands aside.
3. Screen level is next. `primary` is the laptop panel and the focus is on the
   big display, so it does not match.
4. The `os` binding runs: the window tiles to the left half.

Move the same window to the laptop panel and press it again: the window scope
no longer matches (wrong screen), the screen scope now does, and it centres
instead. Focus Notepad and press it: only `os` matches, and it tiles.

---

## Coexistence

* **The portal.** `openspan_portal.py` runs its own hooks in a separate process
  and announces capture through the `Local\EsotericOS.InputCaptureLease` mutex.
  While the portal holds capture your keystrokes are going to another device
  and never reach this process's hook, so scripts are silent — correctly, and
  without any coordination code. The router already has the
  `has_input_capture_lease` flag for the in-process case; nothing sets it yet,
  so a script cannot currently be suppressed on demand.
* **`RegisterHotKey`.** Nothing in EsotericOS calls it today; every global chord
  is claimed by swallowing inside a low-level hook, which is why scoping works
  at all. If a `RegisterHotKey` chord is ever registered, it wins outright —
  Windows dispatches it before any `WH_KEYBOARD_LL` hook sees the key, and a
  script bound to the same chord will simply never fire. That collision cannot
  be won from here; pick a different chord.
* **Built-in shortcuts.** Scripts sit at consumer priority 40, ahead of the
  tiling host at 50, so a script takes a chord back from a shipped default.
  `pass` hands it back.

## Elevation

The app runs elevated by launch invariant, so a script runs at high integrity:
`send` can type into elevated windows (a Medium-integrity sender cannot), and
`window` verbs can move elevated windows. Nothing here widens that — the verb
set cannot launch a program, run a shell, or take a path, so a script's whole
authority is "press keys, move the focused window, ask the broker to open one
catalog id it already knows".
