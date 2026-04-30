#!/bin/bash
# =============================================================
# Printess / ORCA Pi Setup & SSH Hardening Script
# Run as: bash install.sh
# =============================================================

set -e  # Exit immediately on any error

# --- CONFIG — edit these before running ---
PI_USER="${SUDO_USER:-pi}"                          # Username running the script
PROJECT_DIR="/home/${PI_USER}/printess"             # Where orca.py lives
SCRIPT_NAME="orca.py"                               # Your main Python script
SERVICE_NAME="printer"                              # systemd service name
SSH_PORT=22                                         # Change to e.g. 2222 for non-standard port
# ------------------------------------------

echo ""
echo "============================================="
echo "  ORCA / Printess Pi Installer"
echo "  User:    $PI_USER"
echo "  Project: $PROJECT_DIR"
echo "============================================="
echo ""

# -----------------------------------------------
# 1. SYSTEM UPDATE & DEPENDENCIES
# -----------------------------------------------
echo "[1/6] Updating system and installing dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    ufw \
    fail2ban \
    unattended-upgrades

# Install Python libraries into a virtualenv to avoid system conflicts
VENV_DIR="${PROJECT_DIR}/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "      Creating Python virtualenv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

echo "      Installing Python dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install \
    rich \
    pyserial

# pynput is optional — only useful if you have a keyboard attached
# On headless Pi it will fail gracefully inside orca.py
"${VENV_DIR}/bin/pip" install pynput || echo "      (pynput install failed — jog mode will be unavailable, this is OK headless)"

# -----------------------------------------------
# 2. SERIAL PORT ACCESS
# -----------------------------------------------
echo "[2/6] Granting $PI_USER access to serial ports..."
sudo usermod -aG dialout "$PI_USER"

# -----------------------------------------------
# 3. SYSTEMD SERVICE
# -----------------------------------------------
echo "[3/6] Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# BUG FIX: original used markdown hyperlink syntax inside the service file
# e.g. After=[network.target](http://network.target) — systemd cannot parse that.
# Also used >> which appends instead of replacing, and missed sudo on the echo.
# Fixed: write the file cleanly with sudo tee, correct plain-text values.

# Back up existing service if present
if [ -f "$SERVICE_FILE" ]; then
    echo "      Backing up existing service to ${SERVICE_FILE}.old"
    sudo cp "$SERVICE_FILE" "${SERVICE_FILE}.old"
fi

sudo tee "$SERVICE_FILE" > /dev/null << UNIT
[Unit]
Description=ORCA 3D Printer Control Script
After=network.target

[Service]
User=${PI_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${VENV_DIR}/bin/python3 ${PROJECT_DIR}/${SCRIPT_NAME}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl start "${SERVICE_NAME}.service"

echo "      Service installed and started."
echo "      View logs with:  journalctl -u ${SERVICE_NAME}.service -f"

# -----------------------------------------------
# 4. SSH HARDENING
# -----------------------------------------------
echo "[4/6] Hardening SSH..."

SSHD_CONFIG="/etc/ssh/sshd_config"

# Back up original sshd_config
if [ ! -f "${SSHD_CONFIG}.orig" ]; then
    sudo cp "$SSHD_CONFIG" "${SSHD_CONFIG}.orig"
    echo "      Original sshd_config backed up to ${SSHD_CONFIG}.orig"
fi

# Apply hardened settings
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf > /dev/null << SSH
# --- ORCA Pi SSH Hardening ---

# Change port to reduce automated scanning (update SSH_PORT in install.sh)
Port ${SSH_PORT}

# Disable root login completely
PermitRootLogin no

# Disable password authentication — key-based only
# IMPORTANT: Add your public key to ~/.ssh/authorized_keys BEFORE enabling this
# then set to 'no'. Leaving as 'yes' for first-time setup safety.
PasswordAuthentication yes

# Disable empty passwords
PermitEmptyPasswords no

# Only allow our specific user to SSH in
AllowUsers ${PI_USER}

# Disable unused and risky authentication methods
ChallengeResponseAuthentication no
KerberosAuthentication no
GSSAPIAuthentication no
X11Forwarding no
PermitUserEnvironment no

# Reduce login timeout and max attempts
LoginGraceTime 30
MaxAuthTries 3
MaxSessions 3

# Disconnect idle sessions after 5 minutes (ClientAlive* sends keepalives)
ClientAliveInterval 300
ClientAliveCountMax 2

# Disable .rhosts and host-based authentication
IgnoreRhosts yes
HostbasedAuthentication no

# Only use modern, secure ciphers and algorithms
Ciphers aes256-gcm@openssh.com,chacha20-poly1305@openssh.com,aes256-ctr
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org

# Log more verbosely for auditing
LogLevel VERBOSE
SSH

# Validate config before reloading — avoids locking yourself out
echo "      Validating SSH config..."
if sudo sshd -t; then
    sudo systemctl reload sshd
    echo "      SSH hardened and reloaded."
else
    echo "ERROR: sshd config test failed. Reverting hardening file."
    sudo rm /etc/ssh/sshd_config.d/99-hardening.conf
    exit 1
fi

# -----------------------------------------------
# 5. FIREWALL (UFW)
# -----------------------------------------------
echo "[5/6] Configuring firewall..."

# Allow only SSH (on the configured port) and block everything else inbound
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow "${SSH_PORT}/tcp" comment "SSH"
# Uncomment the line below if you want OctoPrint or a web UI accessible on the LAN:
# sudo ufw allow 5000/tcp comment "OctoPrint / web UI"
sudo ufw --force enable

echo "      Firewall enabled. Allowed inbound: SSH on port ${SSH_PORT}."

# -----------------------------------------------
# 6. FAIL2BAN
# -----------------------------------------------
echo "[6/6] Configuring fail2ban..."

sudo tee /etc/fail2ban/jail.d/sshd-local.conf > /dev/null << F2B
[sshd]
enabled  = true
port     = ${SSH_PORT}
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 4
bantime  = 3600
findtime = 600
F2B

sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

echo "      fail2ban active — 4 failed attempts = 1 hour ban."

# -----------------------------------------------
# DONE
# -----------------------------------------------
echo ""
echo "============================================="
echo "  Installation complete!"
echo ""
echo "  IMPORTANT NEXT STEPS:"
echo ""
echo "  1. Add your SSH public key to the Pi:"
echo "     ssh-copy-id -p ${SSH_PORT} ${PI_USER}@<pi-ip>"
echo ""
echo "  2. Test login with your key in a NEW terminal"
echo "     before closing this session."
echo ""
echo "  3. Once key login works, disable password auth:"
echo "     Edit /etc/ssh/sshd_config.d/99-hardening.conf"
echo "     Set:  PasswordAuthentication no"
echo "     Then: sudo systemctl reload sshd"
echo ""
echo "  4. Reboot so the dialout group change takes effect:"
echo "     sudo reboot"
echo ""
echo "  Service logs:  journalctl -u ${SERVICE_NAME}.service -f"
echo "  SSH ban list:  sudo fail2ban-client status sshd"
echo "  Firewall:      sudo ufw status verbose"
echo "============================================="
echo ""
