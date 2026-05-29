import os
import time
import serial # Requires: pip install pyserial

def main():
    # 1. Locate the 'gcode' folder
    folder_name = "gcode"
    if not os.path.exists(folder_name):
        print(f"Error: Could not find a folder named '{folder_name}' in the current directory.")
        return

    # Look for valid G-code file formats (.txt or .gcode)
    valid_extensions = ('.txt', '.gcode')
    files = [f for f in os.listdir(folder_name) if f.lower().endswith(valid_extensions)]

    if not files:
        print(f"No valid files ({', '.join(valid_extensions)}) found in '{folder_name}'.")
        return

    # 2. Display files and get user selection
    print("\n--- Available G-Code Files ---")
    for i, f in enumerate(files):
        print(f"[{i + 1}] {f}")

    while True:
        try:
            choice = int(input("\nSelect a file number to print: "))
            if 1 <= choice <= len(files):
                selected_file = files[choice - 1]
                break
            else:
                print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a valid number.")

    file_path = os.path.join(folder_name, selected_file)

    # 3. Get Serial Port 
    print("\nCommon Mac ports look like: /dev/tty.usbmodem... or /dev/cu.usbmodem...")
    serial_port = input("Enter your printer's serial port: ").strip()

    # FORCE Mac OS to use tty instead of cu for stability during minor disconnects
    if serial_port.startswith("/dev/cu."):
        serial_port = serial_port.replace("/dev/cu.", "/dev/tty.")
        print(f"[*] Auto-corrected to TTY port for Mac stability: {serial_port}")

    # The Printess runs at 115200 baud
    baud_rate = 115200

    # 4. Connect and send the code using the "ping-pong" protocol
    print(f"\nConnecting to {serial_port} at {baud_rate} baud...")
    
    try:
        # Timeout set to 300 seconds (5 mins) to allow long physical moves (like G28 homing) to finish.
        ser = serial.Serial(serial_port, baud_rate, timeout=300)
        
        # Wait for the printer board to initialize after opening the serial connection
        time.sleep(2) 

        # Flush any startup junk in the buffer
        ser.reset_input_buffer()
        
        # Send a wake-up signal to Marlin
        ser.write(b"\r\n\r\n")
        time.sleep(2)
        ser.reset_input_buffer()

        print(f"Successfully connected! Opening {selected_file}...\n")
        print("-" * 30)

        with open(file_path, 'r') as f:
            lines = f.readlines()

        for line in lines:
            # Clean the line: remove comments (anything after ';') and surrounding whitespace
            clean_line = line.split(';')[0].strip()

            # Skip entirely blank lines or lines that only had a comment
            if not clean_line:
                continue

            # Send the command to the printer
            command = clean_line + '\n'
            ser.write(command.encode('utf-8'))
            print(f"Sent: {clean_line}")

            # The Bulletproof Loop: Wait for the printer to reply with "ok" before continuing
            while True:
                # Use errors='replace' so garbled electrical noise bytes don't crash Python
                response = ser.readline().decode('utf-8', errors='replace').strip()
                
                if response:
                    # Filter out the annoying 'busy: processing' spam so you can actually read your console
                    if 'busy: processing' not in response.lower():
                        if 'ok' not in response.lower():
                            print(f"  -> Printer: {response}")
                
                # Break the loop and send the next line once 'ok' is received
                if 'ok' in response.lower():
                    # Micro-delay to prevent flooding the USB chip on back-to-back fast commands
                    time.sleep(0.005)
                    break

        print("\n" + "-" * 30)
        print("Print complete!")

    except serial.SerialException as e:
        print(f"\n[!] Serial Error: {e}")
        print("Check your port name, ensure Pronterface is closed, and verify your power-up sequence to prevent brownouts.")
    except KeyboardInterrupt:
        print("\n[!] Print interrupted by user (Emergency Stop).")
    finally:
        # Safely close the serial port when done or if an error occurs
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Connection closed.")

if __name__ == "__main__":
    main()