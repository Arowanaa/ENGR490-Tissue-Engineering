
# copy old 
sudo cp /etc/systemd/system/printer.service /etc/systemd/system/printer.service.old


# add service
echo “[Unit]
Description=My Custom 3D Printer Control Script
After=network.target

[Service]
# Replace 'pi' with your actual username if different
User=pi
WorkingDirectory=/home/pi/your_project_folder
# Path to your script
ExecStart=/usr/bin/python3 /home/pi/your_project_folder/your_script.py
# Restart the script automatically if it crashes
Restart=always
RestartSec=5
# Ensures logs are sent to the system journal immediately
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target” >> /etc/systemd/system/printer.service

# start service
sudo systemctl daemon-reload
sudo systemctl enable printer.service
sudo systemctl start printer.service

# view output 
journalctl -u printer.service -f

