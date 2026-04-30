#!/bin/bash
# =============================================================
# Printess Pi Initial Setup Script
# Run as root: sudo bash setup.sh
# Must be run over ETHERNET before switching to WiFi
# =============================================================

set -e  # Exit on any error

# --- CONFIG ---
SSH_PORT=2244
# --------------

echo ""
echo "============================================="
echo "  Printess Pi Setup"
echo "  SSH port: $SSH_PORT"
echo "  NOTE: Run this over ethernet only"
echo "============================================="
echo ""

# -----------------------------------------------
# 1. UPDATES
# -----------------------------------------------
echo "[1/5] Running system updates..."
apt update && apt upgrade -y

# Automatic security updates
echo "[1/5] Enabling unattended upgrades..."
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades

# -----------------------------------------------
# 2. SSH
# -----------------------------------------------
# BUG FIX: original used 'Git' (capital G) which fails — git is case-sensitive on Linux
# BUG FIX: openssh-server is already installed on Raspberry Pi OS by default,
#          but we install/enable it here in case it was removed
echo "[2/5] Configuring SSH..."
apt install -y openssh-server
systemctl enable ssh
systemctl start ssh

# -----------------------------------------------
# 3. PACKAGES
# -----------------------------------------------
echo "[3/5] Installing packages..."
apt install -y \
    git \
    python3 \
    python3-pip \
    python3-venv \
    fail2ban \
    ufw

# Clone the repo
# BUG FIX: 'cd /Desktop' doesn't exist on Pi OS — Desktop is at ~/Desktop
# and doesn't exist at all on Lite. Clone to home directory instead.
REPO_DIR="/home/pi/ENGR490-Tissue-Engineering"
if [ ! -d "$REPO_DIR" ]; then
    echo "      Cloning repo..."
    git clone https://github.com/Ephemerill/ENGR490-Tissue-Engineering.git "$REPO_DIR"
else
    echo "      Repo already exists, pulling latest..."
    git -C "$REPO_DIR" pull
fi

# Install Python dependencies
echo "      Installing Python dependencies..."
python3 -m venv "${REPO_DIR}/venv"
"${REPO_DIR}/venv/bin/pip" install --upgrade pip
"${REPO_DIR}/venv/bin/pip" install rich pyserial
"${REPO_DIR}/venv/bin/pip" install pynput || echo "      (pynput skipped — OK on headless)"

# Grant serial port access
usermod -aG dialout pi

# -----------------------------------------------
# 4. FAIL2BAN
# -----------------------------------------------
echo "[4/5] Configuring fail2ban..."

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
# 5. FIREWALL
# -----------------------------------------------
# IMPORTANT: This section is safe to run over ethernet.
# We allow SSH on your custom port and ethernet (eth0) traffic
# BEFORE enabling ufw, so you will not lose your current session.
# WiFi (wlan0) is locked down — open it up once you have WiFi working
# by running the commands printed at the end of this script.
echo "[5/5] Configuring firewall..."

ufw --force reset              # Start from a clean state
ufw default deny incoming
ufw default allow outgoing

# Allow SSH on custom port from any interface (keeps ethernet session alive)
# BUG FIX: original opened port 80 with no web server installed — removed
ufw allow "${SSH_PORT}/tcp" comment "SSH"

# Allow all traffic in on ethernet interface (safe while you're on ethernet)
ufw allow in on eth0 comment "Ethernet - full access"

# Lock down WiFi interface — traffic blocked until you're ready
ufw deny in on wlan0 comment "WiFi - locked until configured"

ufw --force enable

# -----------------------------------------------
# DONE
# -----------------------------------------------
echo ""
echo "============================================="
echo "  Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. Reboot so group changes take effect:"
echo "     sudo reboot"
echo ""
echo "  2. After reboot, connect your SSH key:"
echo "     ssh-copy-id -p ${SSH_PORT} pi@<pi-ip>"
echo ""
echo "  3. When you're ready to use WiFi, run:"
echo "     sudo ufw delete deny in on wlan0"
echo "     sudo ufw allow in on wlan0 comment 'WiFi'"
echo "     sudo ufw reload"
echo ""
echo "  Repo location:   $REPO_DIR"
echo "  Run orca.py:     cd $REPO_DIR && venv/bin/python3 orca.py"
echo "  SSH ban list:    sudo fail2ban-client status sshd"
echo "  Firewall status: sudo ufw status verbose"
echo "============================================="
echo ""
