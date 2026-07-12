#!/bin/bash
# 1) btusb module: never autosuspend the Bluetooth controller
echo 'options btusb enable_autosuspend=0' > /etc/modprobe.d/btusb-noautosuspend.conf
# 2) global USB autosuspend off via kernel cmdline (belt-and-suspenders)
if [ -f /etc/default/grub ]; then
  if ! grep -q 'usbcore.autosuspend=-1' /etc/default/grub; then
    sed -i 's#^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"#\1 usbcore.autosuspend=-1"#' /etc/default/grub
    update-grub 2>&1 | tail -1
  fi
fi
echo '=== modprobe.d ==='
cat /etc/modprobe.d/btusb-noautosuspend.conf
echo '=== grub cmdline ==='
grep '^GRUB_CMDLINE_LINUX_DEFAULT' /etc/default/grub 2>/dev/null || echo 'no grub default file'
