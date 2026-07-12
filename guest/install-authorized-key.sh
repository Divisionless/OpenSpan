#!/bin/bash
# Install the OpenSpan host's public key so the Windows control app can SSH
# into this VM (the app talks to the guest over ssh -p 2222 -i id_openspan
# root@127.0.0.1 for every status poll, BT action, and audio control).
#
# Run this ONCE inside the VM, as root. The VM provisioner calls it; you can
# also run it by hand. The host's public key (id_openspan.pub) is supplied
# either as $1 (a path) or on stdin:
#
#   bash install-authorized-key.sh /media/host/id_openspan.pub
#   cat id_openspan.pub | bash install-authorized-key.sh
#
# It installs openssh-server if missing, appends the key (de-duplicated),
# and enables key-based root login. It never prints or stores a private key.
set -e

command -v sshd >/dev/null 2>&1 || {
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y openssh-server >/dev/null 2>&1 || true
}

mkdir -p /root/.ssh
chmod 700 /root/.ssh

if [ -n "$1" ] && [ -f "$1" ]; then
  KEY_IN=$(cat "$1")
else
  KEY_IN=$(cat)   # from stdin
fi

case "$KEY_IN" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-*)
    : ;;  # looks like a public key
  *)
    echo "refusing: input is not an OpenSSH public key" >&2
    exit 1 ;;
esac

touch /root/.ssh/authorized_keys
printf '%s\n' "$KEY_IN" >> /root/.ssh/authorized_keys
sort -u /root/.ssh/authorized_keys -o /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# allow root login by key (not password), ensure pubkey auth is on
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' \
  /etc/ssh/sshd_config
grep -q '^PermitRootLogin' /etc/ssh/sshd_config || \
  echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' \
  /etc/ssh/sshd_config
grep -q '^PubkeyAuthentication' /etc/ssh/sshd_config || \
  echo 'PubkeyAuthentication yes' >> /etc/ssh/sshd_config

systemctl enable ssh  >/dev/null 2>&1 || systemctl enable sshd  >/dev/null 2>&1 || true
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true

echo "OpenSpan: host key installed in /root/.ssh/authorized_keys; sshd ready."
