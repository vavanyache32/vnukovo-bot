#!/usr/bin/env bash
# VPS bootstrap. Run as root once. Idempotent-ish.
set -euo pipefail

apt-get update
apt-get upgrade -y
apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg ufw fail2ban unattended-upgrades \
    rsync tini

# Docker (official repo)
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
fi

# botuser
id -u botuser >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker botuser

# UFW
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
# webhook (optional)
ufw allow 443/tcp
yes | ufw enable

# fail2ban
systemctl enable --now fail2ban

# unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

mkdir -p /opt/vnukovo-bot
chown -R botuser: /opt/vnukovo-bot

echo "Provision done. Now run as botuser: bash deploy/scripts/install.sh"
