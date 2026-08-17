echo === marker ===
ls -la /run/openspan/btready.done 2>/dev/null || echo MARKER ABSENT
echo === btready journal this boot ===
journalctl -b --no-pager 2>/dev/null | grep -iE "btready|skip" | head -12
echo === wireplumber unit this boot ===
journalctl -b -u openspan-wireplumber --no-pager 2>/dev/null | grep -iE "started|stopp" | head -6
echo === Onn state ===
bluetoothctl info B3:BD:E8:69:E5:59 2>/dev/null | grep -E "Alias|Paired|Connected"
echo === a2dp transport ===
busctl --system call org.bluez / org.freedesktop.DBus.ObjectManager GetManagedObjects 2>/dev/null | grep -c MediaTransport1
