"""Regression guard for openspan_keymap.json and OpenSpan's own PC-side chords.

Why this file exists. EsotericOS shipped a keyboard preset that rewrote Ctrl+A
to Home, and its test suite ASSERTED that behaviour -- so the bug shipped with a
passing test certifying it. We have the same shape of exposure: a JSON keymap
the "Edit keymap" button opens in Notepad, a loader that SILENTLY DROPS tokens
it does not recognise, and a matcher where first match wins.

So this guard checks the shipped file against rules that describe what a keymap
may not do, never against a recording of what it currently does. It reads the
RAW json as well as the loaded form, because the interesting failures are the
ones the loader has already swallowed by the time it returns.

Creates no Tk root, installs no hook, sends nothing.
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import openspan_portal as P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYMAP = os.path.join(ROOT, "openspan_keymap.json")

# The modifier names _phys_mod_names() can actually emit. A 'from' naming
# anything else can never match -- or worse, is dropped and matches a BARE key.
PHYSICAL = frozenset({"ctrl", "shift", "alt", "win"})
# What a 'to' may ask the target for.
TARGET_MODS = frozenset({"ctrl", "shift", "alt", "cmd", "gui", "win"})

# Chords the hands do without looking. A shipped default may change their
# MODIFIERS -- that is the whole point of the iPad map -- but never the KEY.
UNIVERSAL_KEYS = frozenset({"a", "c", "v", "x", "z", "s", "f", "p",
                            "n", "w", "t", "q"})

# Chords no mapping may name at all.
SAFETY_CHORDS = [
    (frozenset({"ctrl", "alt"}), "delete"),
    (frozenset({"ctrl", "shift"}), "esc"),
    (frozenset({"ctrl", "shift"}), "escape"),
    (frozenset({"win"}), "l"),
    (frozenset({"alt"}), "f4"),
    (frozenset({"alt"}), "tab"),
]

# EsotericOS's published claim list, docs/INTEROP.md "What EsotericOS claims".
# Transcribed, not imported: if their doc changes, this test should be updated
# deliberately by a person who read the change.
ESOTERIC_CHORDS = {
    ("ctrl", "alt", "="), ("ctrl", "alt", "-"), ("ctrl", "alt", "0"),
    ("ctrl", "win", "space"), ("ctrl", "alt", "space"),
    ("ctrl", "shift", "v"), ("ctrl", "alt", "v"),
    ("ctrl", "alt", "l"), ("ctrl", "alt", "c"), ("ctrl", "alt", "p"),
    ("ctrl", "alt", "g"), ("ctrl", "alt", "k"), ("ctrl", "alt", "r"),
    ("ctrl", "alt", "a"), ("alt", "`"),
    ("ctrl", "alt", "up"), ("ctrl", "alt", "down"),
    ("ctrl", "space"),
    ("win", "shift", "3"), ("win", "shift", "4"), ("win", "shift", "5"),
    ("win", "left"), ("win", "right"), ("win", "up"), ("win", "down"),
    # Single-key and numpad claims. Neither can collide with anything OpenSpan
    # takes -- ours are all three- and four-key chords -- but the transcription
    # is the record of what was checked, so it is complete rather than
    # convenient. Their `Alt`+wheel is a mouse gesture and is covered in
    # docs/INTEROP.md instead; a wheel is not expressible here.
    ("space",),
} | {("win", "alt", f"numpad{n}") for n in range(1, 10)}
# What OpenSpan's keyboard hook swallows from Windows, from _kbd_proc.
# Ctrl+Alt+Q and Ctrl+Alt+I fire only while NOTHING is captured -- which is
# exactly when EsotericOS is live -- so they count.
OPENSPAN_CHORDS = {
    ("ctrl", "alt", "q"), ("ctrl", "alt", "i"),
    ("ctrl", "alt", "shift", "v"), ("ctrl", "alt", "shift", "c"),
}

FAILURES = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (
        "" if condition or not detail else f"\n       {detail}"))
    if not condition:
        FAILURES.append(name)


def load_raw():
    with open(KEYMAP, encoding="utf-8") as f:
        return json.load(f)


def tokens(seq):
    return [str(t).lower() for t in seq]


def keys_of(toks):
    return [t for t in toks if t in P.NAME_TO_USAGE]


def mods_of(toks):
    return [t for t in toks if t in P.IPAD_MOD_BIT]


def main():
    km = load_raw()
    overrides = km.get("overrides", [])
    remap = {str(k).lower(): str(v).lower()
             for k, v in km.get("modifier_remap", {}).items()}

    print(f"-- {len(overrides)} override(s), remap={remap or 'none'} --\n")

    seen_from = {}
    for i, ov in enumerate(overrides):
        tag = f"override[{i}] {ov.get('from')} -> {ov.get('to')}"
        frm, to = tokens(ov.get("from", [])), tokens(ov.get("to", []))

        # R2 -- nothing may be silently dropped. This is the one that catches a
        # typo'd modifier turning an override into a BARE-KEY hijack: 'control'
        # is not a name the loader knows, so {"from":["control","a"]} loads as
        # "plain a", and every unmodified 'a' typed at the device becomes Cmd+A.
        unknown = [t for t in frm + to
                   if t not in P.NAME_TO_USAGE and t not in P.IPAD_MOD_BIT]
        check(f"R2 every token resolves -- {tag}", not unknown,
              f"unrecognised, and SILENTLY DROPPED by the loader: {unknown}")

        # R1 -- 'from' modifiers must be ones the hook can actually report.
        bad = [t for t in mods_of(frm) if t not in PHYSICAL]
        check(f"R1 'from' modifiers are physical -- {tag}", not bad,
              f"{bad} can never be reported by _phys_mod_names(); dead rule")

        # R3 -- a 'to' with no key sends a modifier-only report: the chord is
        # swallowed and the target silently loses it.
        check(f"R3 'to' names a key -- {tag}", bool(keys_of(to)),
              "empty key list -> modifier-only report -> chord swallowed")
        check(f"R3b 'from' names exactly one key -- {tag}",
              len(keys_of(frm)) == 1,
              f"matcher only tests single-key combos; got {keys_of(frm)}")

        # R4 -- first match wins, silently.
        sig = (frozenset(mods_of(frm)), frozenset(keys_of(frm)))
        check(f"R4 'from' is unique -- {tag}", sig not in seen_from,
              f"already claimed by override[{seen_from.get(sig)}]")
        seen_from.setdefault(sig, i)

        # R5 -- THE ESOTERICOS RULE. Modifiers may be rewritten; the key may
        # not. ctrl+a -> cmd+a passes. ctrl+a -> home is the shipped bug.
        fk, tk_ = keys_of(frm), keys_of(to)
        if fk and fk[0] in UNIVERSAL_KEYS:
            check(f"R5 universal chord keeps its key -- {tag}",
                  bool(tk_) and tk_[0] == fk[0],
                  f"'{fk[0]}' redefined as '{(tk_ or ['<nothing>'])[0]}' -- a "
                  "shipped default may change a universal chord's MODIFIERS, "
                  "never what key it means")

        # R6 -- safety chords are not ours to touch.
        fmods = frozenset(mods_of(frm))
        hit = [(m, k) for m, k in SAFETY_CHORDS
               if m <= fmods and k in fk]
        check(f"R6 names no safety chord -- {tag}", not hit, f"{hit}")

    # R7 -- modifier_remap. Scoped to EXPLICIT entries on purpose: physical Win
    # and remapped Alt both arrive as cmd (IPAD_MOD_BIT maps win -> 0x08), and
    # that is intended, so a rule counting implicit identity mappings would
    # fail on correct behaviour. Asserting a bug is how the incident happened.
    bad_k = [k for k in remap if k not in PHYSICAL]
    bad_v = [v for v in remap.values() if v not in TARGET_MODS]
    check("R7 remap keys are physical modifiers", not bad_k, f"{bad_k}")
    check("R7 remap values are target modifiers", not bad_v, f"{bad_v}")
    vals = list(remap.values())
    check("R7 no two explicit remaps collide",
          len(vals) == len(set(vals)),
          f"{vals} -- two physical modifiers merged into one target modifier")

    # R8 -- coexistence. OpenSpan's hook swallows these from Windows whether or
    # not it is capturing, so the lease does not cover them; they must stay
    # disjoint from what EsotericOS claims. (docs/INTEROP.md)
    clash = {c for c in OPENSPAN_CHORDS
             if tuple(sorted(c)) in {tuple(sorted(e)) for e in ESOTERIC_CHORDS}}
    check("R8 no chord collision with EsotericOS", not clash, f"{clash}")

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S)")
        raise SystemExit(1)
    print("RESULT: ALL PASS -- all keymap safety rules hold")


if __name__ == "__main__":
    main()
