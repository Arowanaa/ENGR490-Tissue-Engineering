#!/bin/bash
# =============================================================
# Printess / ORCA Pi Setup Script
# Run over ETHERNET as root: sudo bash setup.sh
# =============================================================

set -e

# --- CONFIG — edit before running ---
PI_USER="${SUDO_USER:-pi}"
PROJECT_DIR="/home/${PI_USER}/ENGR490-Tissue-Engineering"
SCRIPT_NAME="orca.py"
SERVICE_NAME="printer"
SSH_PORT=2244
# ------------------------------------

echo ""
echo "============================================="
echo "  Printess Pi Setup"
echo "  User:    $PI_USER"
echo "  Project: $PROJECT_DIR"
echo "  SSH Port: $SSH_PORT"
echo "  NOTE: Run this over ethernet"
echo "============================================="
echo ""

# -----------------------------------------------
# 1. UPDATES
# -----------------------------------------------
echo "[1/6] System updates..."
apt-get update -y && apt-get upgrade -y
apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades

# -----------------------------------------------
# 2. PACKAGES & REPO
# -----------------------------------------------
echo "[2/6] Installing packages and cloning repo..."
apt-get install -y git python3 python3-pip python3-venv ufw fail2ban openssh-server

if [ ! -d "$PROJECT_DIR" ]; then
    git clone https://github.com/Ephemerill/ENGR490-Tissue-Engineering.git "$PROJECT_DIR"
else
    git -C "$PROJECT_DIR" pull
fi

# Virtualenv + Python deps
python3 -m venv "${PROJECT_DIR}/venv"
"${PROJECT_DIR}/venv/bin/pip" install --upgrade pip
"${PROJECT_DIR}/venv/bin/pip" install rich pyserial
"${PROJECT_DIR}/venv/bin/pip" install pynput || echo "      (pynput skipped — OK headless)"

# Serial port access
usermod -aG dialout "$PI_USER"

# -----------------------------------------------
# 3. SYSTEMD SERVICE
# -----------------------------------------------
echo "[3/6] Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
[ -f "$SERVICE_FILE" ] && cp "$SERVICE_FILE" "${SERVICE_FILE}.old"

tee "$SERVICE_FILE" > /dev/null << UNIT
[Unit]
Description=ORCA 3D Printer Control Script
After=network.target

[Service]
User=${PI_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/${SCRIPT_NAME}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl start "${SERVICE_NAME}.service"

# -----------------------------------------------
# 4. SSH
# -----------------------------------------------
echo "[4/6] Hardening SSH..."

systemctl enable ssh
systemctl start ssh

[ ! -f /etc/ssh/sshd_config.orig ] && cp /etc/ssh/sshd_config /etc/ssh/sshd_config.orig

tee /etc/ssh/sshd_config.d/99-hardening.conf > /dev/null << SSH
Port ${SSH_PORT}
PermitRootLogin no
AllowUsers ${PI_USER}
PasswordAuthentication yes
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
SSH

# Validate before reloading — won't lock you out on a typo
sshd -t && systemctl reload sshd || {
    echo "ERROR: sshd config invalid — reverting"
    rm /etc/ssh/sshd_config.d/99-hardening.conf
    exit 1
}

# -----------------------------------------------
# 5. FIREWALL
# -----------------------------------------------
# Safe to run over ethernet — eth0 is fully allowed BEFORE ufw enables,
# so your current session stays alive. wlan0 is locked until you're ready.
echo "[5/6] Configuring firewall..."

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp"    comment "SSH"
ufw allow in on eth0           comment "Ethernet - full access"
ufw deny  in on wlan0          comment "WiFi - locked until configured"
ufw --force enable

# -----------------------------------------------
# 6. FAIL2BAN
# -----------------------------------------------
echo "[6/6] Configuring fail2ban..."

tee /etc/fail2ban/jail.d/sshd-local.conf > /dev/null << F2B
[sshd]
enabled  = true
port     = ${SSH_PORT}
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 4
bantime  = 3600
findtime = 600
F2B

systemctl enable fail2ban
systemctl restart fail2ban

# -----------------------------------------------
# DONE
# -----------------------------------------------
echo ""
echo "============================================="
echo "  Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Reboot:  sudo reboot"
echo "  2. SSH in:  ssh -p ${SSH_PORT} ${PI_USER}@<pi-ip>"
echo ""
echo "  When ready to enable WiFi:"
echo "    sudo ufw delete deny in on wlan0"
echo "    sudo ufw allow in on wlan0"
echo "    sudo ufw reload"
echo ""
echo "  Logs:     journalctl -u ${SERVICE_NAME}.service -f"
echo "  Bans:     sudo fail2ban-client status sshd"
echo "  Firewall: sudo ufw status verbose"
echo "============================================="
echo ""
