#!/bin/bash
# OpenSpan headless PipeWire audio stack, run as a root system service.
# Provides a fixed session bus at /run/user/0/bus so other units and
# SSH diagnostics can attach to the same graph.
# /run/openspan, NOT /run/user/0: the latter is systemd-logind territory and
# gets torn down/recreated on every root login (SSH), wiping PipeWire's socket.
export XDG_RUNTIME_DIR=/run/openspan
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
rm -f "$XDG_RUNTIME_DIR/bus"
dbus-daemon --session --address="unix:path=$XDG_RUNTIME_DIR/bus" --fork
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
pipewire &
sleep 1
pipewire-pulse &
sleep 1
exec wireplumber
