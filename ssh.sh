echo "[2/5] Configuring SSH..."
apt install -y openssh-server
systemctl enable ssh
systemctl start ssh

# Harden sshd_config
tee /etc/ssh/sshd_config.d/99-hardening.conf > /dev/null << SSH
Port 2244
PermitRootLogin no
AllowUsers pi
PasswordAuthentication yes
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
SSH

# Validate before reloading — avoids locking yourself out
sshd -t && systemctl reload sshd || {
    echo "ERROR: sshd config invalid, reverting"
    rm /etc/ssh/sshd_config.d/99-hardening.conf
    exit 1
}
