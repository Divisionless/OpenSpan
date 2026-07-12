# OpenSpan guest environment. The audio stack (PipeWire/WirePlumber/bridge)
# lives on the persistent systemd USER session bus for root -- linger is
# enabled so /run/user/0 and /run/user/0/bus survive with no login session.
# This is the documented footing for a headless system-wide instance.
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
