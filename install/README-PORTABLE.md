# EsotericOS — portable node

This folder is the whole program. Copy it anywhere on a Windows 10/11 PC and
run `EsotericOS.exe`. There is no installer, no registry setup to do by hand,
and nothing to configure before it will start.

## What happens on first run

Nothing is assumed and nothing is asked:

- It finds **this PC's monitors** and lays them out exactly as Windows has
  them. The Console says how many it found and what they are called.
- There are **no devices** yet, because nothing was carried over from any other
  machine. You add what you actually own.
- It generates **this node's own identity** — 32 random bytes in `node.json`.
  That file is this machine's private key. It is not derived from your PC name,
  your hardware, or anything else that could collide with another node, and it
  is never copied between machines.
- It looks for **other EsotericOS machines on this network** and lists them
  under *Devices ▸ Nodes on this network*.

## Bridge or no bridge

If this PC has VirtualBox and the guest VM, the Bluetooth lanes work here —
driving an iPad or a managed Mac that will not let you install anything.

If it does not, that is a **LAN node** and the app says so:

> no bridge on this node — Bluetooth lanes need VirtualBox + the guest VM;
> LAN lanes do not

Nothing is broken and nothing is retried in the background. Everything that
runs over the network works exactly the same.

## Pairing with another machine

On both PCs, open *Devices ▸ Nodes on this network*. The other machine appears
by name. Press **Pair** on either one; a **six-digit code** appears on both
screens. If they match, press **Same code** on **both** — one side alone does
not pair anything.

The pairing is stored by **key**, not by address. Rename the machine, move it
to a different network, or let its IP change: the pairing survives, because
nothing about it was ever positional.

`peers.json` holds the shared secret for each pairing. Like `node.json`, it
never travels between machines.

## Windows Firewall

Run `bake-in.ps1` **as administrator** once. It does two things:

1. Starts EsotericOS at sign-in (per-user `Run` key).
2. Adds a Windows Firewall rule allowing **the program** `EsotericOS.exe`,
   inbound and outbound, on private networks.

The rule allows the *program*, not a port, and that is deliberate: this node's
LAN service port is assigned by the OS at every launch and is different each
time, so a port rule would be wrong by the next restart.

If you skip that step, the app notices that inbound connections are being
refused and offers **Allow EsotericOS through the firewall** in the window,
which runs the same rule elevated. It only ever runs on that click.

## Updating

Copy a newer `EsotericOS.exe` beside this one and run:

```
powershell -ExecutionPolicy Bypass -File swap-build.ps1
```

It refuses while the binary is running and preserves the outgoing build.

## What is deliberately not in this folder

No configuration, no logs, no keys, no VM, and nothing naming the machine this
package was built on. It is a fresh node, not a clone.
