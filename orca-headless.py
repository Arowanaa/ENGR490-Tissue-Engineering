import math
import os
import platform
import subprocess
import time
import sys
import re
from datetime import datetime

# Platform-specific imports for terminal control
if sys.platform == 'win32':
    import msvcrt
else:
    import select
    import tty
    import termios

# Import Rich
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import IntPrompt, Prompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
except ImportError:
    print("Please install the 'rich' library: pip install rich")
    sys.exit()

# Import pyserial for printer communication
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Please install the 'pyserial' library: pip install pyserial")
    sys.exit()

# Import pynput for raw keyboard hardware monitoring
# BUG FIX #5: pynput crashes on headless Pi (no display/TTY with X or accessibility).
# Guard the import and set a flag so jog mode can degrade gracefully.
PYNPUT_AVAILABLE = False
try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    pass  # Handled at runtime in interactive_jog_menu
except Exception:
    # On headless Linux pynput may raise Xlib/uinput errors at import time
    pass

# Initialize the Rich Console
console = Console()

# ==========================================
# --- CONFIGURATION PARAMETERS ---
# ==========================================
COORDINATE_MODE = "G90"         # 'G90' for Absolute, 'G91' for Relative
EXTRUSION_AXIS = "B"            # The target axis for extrusion ('B' or 'C')
Z_SYRINGE_DIAMETER = 4.9        # Inner diameter in mm (4.9 for 1mL BD syringe)
A_SYRINGE_DIAMETER = 4.9
Z_NOZZLE_DIAMETER = 2           # Nozzle diameter in mm
A_NOZZLE_DIAMETER = 0.2
EXTRUSION_COEFFICIENT = 0.33    # Scaling factor for extrusion

# Auto-Pressurization Settings
DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 0.2
PRESSURIZE_SPEED = 300          # Capped at 300

# Jog Settings
JOG_DISTANCE = 0.2              # Distance in mm per keystroke tick
JOG_SPEED_MM_MIN = 300          # The F-value for jogging speed
HIGH_PRECISION_JOG = True       # Start in high precision mode

# Bed Origin Settings
START_FROM_CENTER = False       # If True, expects bed to start in center, skipping init travel

# Serial Connection Settings
BAUD_RATE = 115200

# ==========================================
# --- SENSORLESS HOMING SETTINGS ---
# Firmware confirmed: Marlin 2.1.2.4 on Octopus V1, M914 + StallGuard enabled.
# v5 firmware homes to MAX endstops (x_max, y_max, z_max).
# Tune SGTHRS values using Option 8 → Tune axis.
# ==========================================
HOME_CURRENT_X   = 400    # mA — reduced current improves stall sensitivity
HOME_CURRENT_Y   = 400
HOME_CURRENT_Z   = 400
RUN_CURRENT_X    = 800    # mA — restored after homing completes
RUN_CURRENT_Y    = 800
RUN_CURRENT_Z    = 800
SGTHRS_X         = 100    # StallGuard threshold 0-255 (tune via Option 8)
SGTHRS_Y         = 100
SGTHRS_Z         = 100
# ==========================================

# --- STATE VARIABLES ---
printer_conn = None
loaded_filepath = None

def display_header():
    splash = r"""
 ____  _____  _____         
/ __ \|  __ \/ ____|   /\   
| |  | | |__) | |       /  \  
| |  | |  _  /| |      / /\ \ 
| |__| | | \ \| |____ / ____ \
\____/|_|  \_\\_____/_/    \_\
"""
    console.print(splash, style="bold cyan")
    console.print("                    [cyan]v1.0.16[/cyan]\n")

def settings_menu():
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE, HIGH_PRECISION_JOG, START_FROM_CENTER
    global SGTHRS_X, SGTHRS_Y, SGTHRS_Z
    global HOME_CURRENT_X, HOME_CURRENT_Y, HOME_CURRENT_Z
    global RUN_CURRENT_X, RUN_CURRENT_Y, RUN_CURRENT_Z

    while True:
        console.clear()
        display_header()

        config_table = Table(show_header=True, header_style="bold yellow", expand=True, title="[bold cyan]Current Configuration[/bold cyan]")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
        config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
        config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
        config_table.add_row("Extrusion Coeff.", str(EXTRUSION_COEFFICIENT), "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]")
        config_table.add_row("Jog Precision", "[green]HIGH[/green]" if HIGH_PRECISION_JOG else "[yellow]LOW[/yellow]", "Start from Center", "[green]ON[/green]" if START_FROM_CENTER else "[red]OFF[/red]")
        config_table.add_row("SGTHRS X", str(SGTHRS_X), "Home Current X", f"{HOME_CURRENT_X} mA")
        config_table.add_row("SGTHRS Y", str(SGTHRS_Y), "Home Current Y", f"{HOME_CURRENT_Y} mA")
        config_table.add_row("SGTHRS Z", str(SGTHRS_Z), "Home Current Z", f"{HOME_CURRENT_Z} mA")
        config_table.add_row("Run Current X", f"{RUN_CURRENT_X} mA", "Run Current Y", f"{RUN_CURRENT_Y} mA")
        config_table.add_row("Run Current Z", f"{RUN_CURRENT_Z} mA", "", "")

        console.print(config_table)
        console.print("\n[bold yellow]--- Options Menu ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Toggle Jog Precision Mode")
        console.print("[5] Toggle Start from Center")
        console.print("[6] Edit Sensorless Homing Currents")
        console.print("[7] Return to Main Menu\n")

        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=["1", "2", "3", "4", "5", "6", "7"])

        if choice == "1":
            new_coeff = Prompt.ask("Enter new Extrusion Coefficient", default=str(EXTRUSION_COEFFICIENT))
            try:
                EXTRUSION_COEFFICIENT = float(new_coeff)
            except ValueError:
                console.print("[bold red]Invalid number. Please enter a valid float.[/bold red]")
                time.sleep(1.5)
        elif choice == "2":
            DO_AUTO_PRESSURIZE = not DO_AUTO_PRESSURIZE
        elif choice == "3":
            COORDINATE_MODE = "G91" if COORDINATE_MODE == "G90" else "G90"
        elif choice == "4":
            HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
        elif choice == "5":
            START_FROM_CENTER = not START_FROM_CENTER
        elif choice == "6":
            _adjust_home_currents()
        elif choice == "7":
            break

def connect_to_printer():
    global printer_conn

    if printer_conn and printer_conn.is_open:
        try:
            printer_conn.close()
        except Exception:
            pass

    ports = serial.tools.list_ports.comports()
    if not ports:
        console.print("[bold red]No serial ports found. Make sure the printer is plugged in.[/bold red]")
        time.sleep(2)
        return

    console.print("[bold cyan]Available Ports:[/bold cyan]")
    for i, port in enumerate(ports):
        console.print(f"[{i + 1}] {port.device} - {port.description}")

    console.print(f"[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select the port to connect to[/bold yellow]", choices=[str(i) for i in range(len(ports) + 1)])

    if choice == 0:
        return

    selected_port = ports[choice - 1].device

    try:
        with console.status(f"[bold green]Connecting to {selected_port} at {BAUD_RATE} baud...", spinner="dots"):
            printer_conn = serial.Serial(selected_port, BAUD_RATE, timeout=2)

            # HARDWARE RESET: Toggling DTR tells the 3D printer board to reset its serial state
            printer_conn.setDTR(False)
            time.sleep(0.05)
            printer_conn.setDTR(True)

            # Clear any garbage leftover in the OS buffers
            printer_conn.reset_input_buffer()
            printer_conn.reset_output_buffer()

            # Send wake up pings
            printer_conn.write(b"\n\n")
            time.sleep(2)
            printer_conn.reset_input_buffer()

            console.print(f"[bold green]Successfully connected to {selected_port}![/bold green]")
            time.sleep(1)
    except Exception as e:
        console.print(f"[bold red]Failed to connect: {e}[/bold red]")
        printer_conn = None
        time.sleep(2)

def reset_printer_board():
    """Forces a hard reboot and serial flush for the printer to clear hangs."""
    global printer_conn

    if not printer_conn:
        console.print("[bold red]Printer not connected! Cannot send reset signal.[/bold red]")
        time.sleep(1.5)
        return

    console.print("[bold yellow]Sending reset signals to printer board...[/bold yellow]")
    try:
        printer_conn.write(b"M112\n")
        time.sleep(0.1)
        printer_conn.write(b"M999\n")

        printer_conn.setDTR(False)
        time.sleep(0.5)
        printer_conn.setDTR(True)

        printer_conn.reset_input_buffer()
        printer_conn.reset_output_buffer()

        console.print("[bold green]Printer board reset successfully! Give it a few seconds to boot up.[/bold green]")
        time.sleep(2)
    except Exception as e:
        console.print(f"[bold red]Failed to reset: {e}[/bold red]")
        console.print("[yellow]Tip: If the port is completely locked, physically unplug the USB cable and plug it back in.[/yellow]")
        time.sleep(3)

# ============================================================
# --- SENSORLESS HOMING HELPERS ---
# ============================================================

def send_and_wait(command, timeout=90):
    """
    Sends a single G-code command and blocks until the printer replies 'ok'.
    Returns True on success, False on timeout or serial error.
    Prints all printer responses so the user can see what's happening.
    """
    try:
        printer_conn.write((command.strip() + '\n').encode('utf-8'))
        start = time.time()
        while True:
            if time.time() - start > timeout:
                console.print(f"[bold red]Timeout ({timeout}s) waiting for 'ok' after: {command.strip()}[/bold red]")
                return False
            if printer_conn.in_waiting > 0:
                response = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                if response:
                    console.print(f"  [dim]{response}[/dim]")
                if 'ok' in response.lower():
                    return True
            time.sleep(0.05)
    except serial.SerialException as e:
        console.print(f"[bold red]Serial error: {e}[/bold red]")
        return False


def _do_sensorless_home(axes):
    """
    Internal: lower current → set SGTHRS → home each axis individually → restore current.
    v5 firmware homes to MAX endstops (x_max, y_max, z_max), so G28 drives toward
    the positive end of each screw.
    """
    axis_params = {
        "X": (HOME_CURRENT_X, RUN_CURRENT_X, SGTHRS_X),
        "Y": (HOME_CURRENT_Y, RUN_CURRENT_Y, SGTHRS_Y),
        "Z": (HOME_CURRENT_Z, RUN_CURRENT_Z, SGTHRS_Z),
    }

    console.print(f"\n[bold cyan]Homing axes: {' '.join(axes)}[/bold cyan]")
    printer_conn.reset_input_buffer()

    # 1. Set reduced homing current for all axes being homed
    current_cmd = "M906 " + " ".join(f"{ax}{axis_params[ax][0]}" for ax in axes)
    console.print(f"[dim]Setting home current → {current_cmd}[/dim]")
    if not send_and_wait(current_cmd, timeout=10):
        console.print("[bold red]Failed to set homing current.[/bold red]")
        time.sleep(2)
        return False

    # 2. Set StallGuard thresholds
    sgthrs_cmd = "M914 " + " ".join(f"{ax}{axis_params[ax][2]}" for ax in axes)
    console.print(f"[dim]Setting SGTHRS → {sgthrs_cmd}[/dim]")
    if not send_and_wait(sgthrs_cmd, timeout=10):
        console.print("[bold red]Failed to set StallGuard threshold.[/bold red]")
        _restore_run_current(axes, axis_params)
        time.sleep(2)
        return False

    # Brief pause — StallGuard needs ~1s to settle after threshold change
    time.sleep(1.5)

    # 3. Home each axis individually (safer than G28 XYZ simultaneously)
    for ax in axes:
        console.print(f"[bold cyan]Homing {ax}...[/bold cyan]")
        if not send_and_wait(f"G28 {ax}", timeout=120):
            console.print(f"[bold red]Homing {ax} failed or timed out.[/bold red]")
            console.print("[yellow]If the axis barely moved → lower SGTHRS. If it slammed without stopping → raise SGTHRS.[/yellow]")
            _restore_run_current(axes, axis_params)
            time.sleep(3)
            return False
        # 2s pause between axes so the StallGuard triggered flag clears
        time.sleep(2)

    # 4. Wait for all buffered hardware moves to complete
    send_and_wait("M400", timeout=30)

    # 5. Restore normal run current
    _restore_run_current(axes, axis_params)
    return True


def _restore_run_current(axes, axis_params):
    restore_cmd = "M906 " + " ".join(f"{ax}{axis_params[ax][1]}" for ax in axes)
    console.print(f"[dim]Restoring run current → {restore_cmd}[/dim]")
    send_and_wait(restore_cmd, timeout=10)


def _tune_sgthrs(axis):
    """
    Interactive StallGuard tuning for one axis.
    Start at 255 (maximum sensitivity), step down until the axis
    travels all the way to the physical end of the screw and stops.
    The highest value that reliably homes is the working threshold.
    """
    global SGTHRS_X, SGTHRS_Y, SGTHRS_Z

    home_current = {"X": HOME_CURRENT_X, "Y": HOME_CURRENT_Y, "Z": HOME_CURRENT_Z}[axis]
    run_current  = {"X": RUN_CURRENT_X,  "Y": RUN_CURRENT_Y,  "Z": RUN_CURRENT_Z }[axis]

    console.print(Panel(
        f"[bold cyan]StallGuard Tuning — {axis} axis[/bold cyan]\n\n"
        "Finds the right sensitivity for your specific motor and screw load.\n\n"
        "[bold yellow]Process:[/bold yellow]\n"
        "  Start at SGTHRS 255 → axis stops almost immediately (too sensitive).\n"
        "  Step down until the axis travels all the way to the physical end and stops cleanly.\n"
        "  The highest value where it reliably homes = your working threshold.\n\n"
        "[bold red]SAFETY:[/bold red] Watch the printer the whole time.\n"
        "If the axis slams repeatedly without stopping, press Ctrl+C immediately.\n\n"
        "Make sure the axis has room to travel toward its MAX end.",
        border_style="yellow"
    ))

    start_val = IntPrompt.ask("Starting SGTHRS (255 = most sensitive)", default=255)
    step      = IntPrompt.ask("Step size per test (20 to start, 5 near target)", default=20)
    current_val = start_val
    last_good_val = None

    console.print(f"\n[bold cyan]Tuning loop for {axis}... enter 'q' at any prompt to stop.[/bold cyan]\n")

    while 0 <= current_val <= 255:
        console.print(f"[bold]Testing {axis} SGTHRS = {current_val}[/bold]")

        send_and_wait(f"M906 {axis}{home_current}", timeout=5)
        send_and_wait(f"M914 {axis}{current_val}", timeout=5)
        time.sleep(1.5)

        console.print(f"[dim]Sending: G28 {axis}[/dim]")
        printer_conn.write(f"G28 {axis}\n".encode('utf-8'))

        start = time.time()
        homed = False
        while time.time() - start < 120:
            if printer_conn.in_waiting > 0:
                resp = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                if resp:
                    console.print(f"  [dim]{resp}[/dim]")
                if 'ok' in resp.lower():
                    homed = True
                    break
            time.sleep(0.05)

        send_and_wait(f"M906 {axis}{run_current}", timeout=5)
        time.sleep(2)

        if not homed:
            console.print("[bold red]No 'ok' received — printer may have timed out or stalled early.[/bold red]")

        result = Prompt.ask(
            f"  Did {axis} travel all the way to the end and stop cleanly?\n"
            "  [bold green](y)[/bold green] yes/save  "
            "[bold yellow](n)[/bold yellow] no/step down  "
            "[bold red](q)[/bold red] quit",
            choices=["y", "n", "q"],
            default="n"
        )

        if result == "q":
            break
        elif result == "y":
            last_good_val = current_val
            console.print(f"[bold green]✓ SGTHRS {current_val} works for {axis}.[/bold green]")
            fine = Prompt.ask("Fine-tune upward to find the true upper edge?", choices=["y", "n"], default="y")
            if fine == "n":
                break
            step = IntPrompt.ask("Fine-tune step size", default=5)
            current_val += step   # walk back up to find the edge
        else:
            current_val -= step

    if last_good_val is not None:
        save = Prompt.ask(
            f"\nSave SGTHRS {last_good_val} as the working value for {axis}?",
            choices=["y", "n"], default="y"
        )
        if save == "y":
            if axis == "X":   SGTHRS_X = last_good_val
            elif axis == "Y": SGTHRS_Y = last_good_val
            elif axis == "Z": SGTHRS_Z = last_good_val
            console.print(f"[bold green]Saved SGTHRS_{axis} = {last_good_val}[/bold green]")
            console.print("[dim]Note: update the SGTHRS constants at the top of this file to make it permanent across restarts.[/dim]")
    else:
        console.print("[bold yellow]No working value found. Try a lower starting value or reduce homing speed in firmware.[/bold yellow]")

    time.sleep(2)


def _adjust_home_currents():
    """Edit homing and run currents for each axis. Called from settings menu and homing menu."""
    global HOME_CURRENT_X, HOME_CURRENT_Y, HOME_CURRENT_Z
    global RUN_CURRENT_X, RUN_CURRENT_Y, RUN_CURRENT_Z

    console.print("\n[bold cyan]Adjust Homing Currents[/bold cyan]")
    console.print("[dim]Lower home current = more sensitive stall detection. Aim for ~40-60% of run current.[/dim]\n")

    HOME_CURRENT_X = IntPrompt.ask("  X home current (mA)", default=HOME_CURRENT_X)
    RUN_CURRENT_X  = IntPrompt.ask("  X run  current (mA)", default=RUN_CURRENT_X)
    HOME_CURRENT_Y = IntPrompt.ask("  Y home current (mA)", default=HOME_CURRENT_Y)
    RUN_CURRENT_Y  = IntPrompt.ask("  Y run  current (mA)", default=RUN_CURRENT_Y)
    HOME_CURRENT_Z = IntPrompt.ask("  Z home current (mA)", default=HOME_CURRENT_Z)
    RUN_CURRENT_Z  = IntPrompt.ask("  Z run  current (mA)", default=RUN_CURRENT_Z)

    console.print("[bold green]Currents updated.[/bold green]")
    time.sleep(1.5)


def sensorless_home_menu():
    """
    Option 8: Sensorless homing using TMC2209 StallGuard.

    Firmware confirmed: Marlin 2.1.2.4 on Octopus V1 with M914 + StallGuard
    enabled (v1-v5 sensorless binaries). v5 homes to MAX endstops.

    Hardware required: DIAG jumpers installed under MOTOR0/MOTOR1/MOTOR2
    on the Octopus board to connect DIAG pins to the endstop inputs.
    """
    global printer_conn

    if not printer_conn or not printer_conn.is_open:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1.5)
        return

    while True:
        console.clear()
        display_header()
        console.print(Panel(
            "[bold cyan]Sensorless Homing — StallGuard (TMC2209)[/bold cyan]\n\n"
            "Drives each axis into its physical end-of-screw. The TMC2209 detects\n"
            "the stall and signals Marlin to stop — no limit switches needed.\n\n"
            f"  X  SGTHRS: [bold yellow]{SGTHRS_X}[/bold yellow]   "
            f"home: [bold yellow]{HOME_CURRENT_X} mA[/bold yellow]   "
            f"run: [bold yellow]{RUN_CURRENT_X} mA[/bold yellow]\n"
            f"  Y  SGTHRS: [bold yellow]{SGTHRS_Y}[/bold yellow]   "
            f"home: [bold yellow]{HOME_CURRENT_Y} mA[/bold yellow]   "
            f"run: [bold yellow]{RUN_CURRENT_Y} mA[/bold yellow]\n"
            f"  Z  SGTHRS: [bold yellow]{SGTHRS_Z}[/bold yellow]   "
            f"home: [bold yellow]{HOME_CURRENT_Z} mA[/bold yellow]   "
            f"run: [bold yellow]{RUN_CURRENT_Z} mA[/bold yellow]\n\n"
            "[bold yellow]HARDWARE REQUIRED:[/bold yellow] DIAG jumpers installed on Octopus\n"
            "under MOTOR0 (X), MOTOR1 (Y), MOTOR2 (Z) driver slots.\n"
            "[bold yellow]FIRMWARE:[/bold yellow] Flash Sensorless_firmware_v5.bin before using this.",
            border_style="cyan"
        ))

        console.print("[bold yellow]--- Sensorless Homing Menu ---[/bold yellow]")
        console.print("[1] Home All Axes (X, Y, Z)")
        console.print("[2] Home X only")
        console.print("[3] Home Y only")
        console.print("[4] Home Z only")
        console.print("[5] Tune SGTHRS — X axis")
        console.print("[6] Tune SGTHRS — Y axis")
        console.print("[7] Tune SGTHRS — Z axis")
        console.print("[8] Adjust homing / run currents")
        console.print("[9] Return to Main Menu\n")

        choice = Prompt.ask("[bold yellow]Choose[/bold yellow]", choices=["1","2","3","4","5","6","7","8","9"])

        if choice == "9":
            break

        elif choice == "1":
            axes = ["X", "Y", "Z"]
            ok = _do_sensorless_home(axes)
            if ok:
                console.print(Panel(
                    "[bold green]✓ Homing complete![/bold green]\n\n"
                    "X, Y, Z are now zeroed at the MAX end of their travel.\n"
                    "Use jog control or G92 to set your working origin from here.",
                    border_style="green"
                ))
            time.sleep(2)

        elif choice in ("2", "3", "4"):
            ax = {"2": "X", "3": "Y", "4": "Z"}[choice]
            ok = _do_sensorless_home([ax])
            if ok:
                console.print(Panel(
                    f"[bold green]✓ {ax} homed.[/bold green]\n"
                    f"Axis {ax} is now zeroed at its MAX end of travel.",
                    border_style="green"
                ))
            time.sleep(2)

        elif choice == "5":
            _tune_sgthrs("X")
        elif choice == "6":
            _tune_sgthrs("Y")
        elif choice == "7":
            _tune_sgthrs("Z")
        elif choice == "8":
            _adjust_home_currents()


# ============================================================
# --- JOG MENU ---
# ============================================================

def interactive_jog_menu():
    global printer_conn, HIGH_PRECISION_JOG

    if not printer_conn:
        console.print("[bold red]Printer not connected! Please connect first.[/bold red]")
        time.sleep(1.5)
        return "quit"

    console.clear()
    display_header()

    mode_str = "[bold green]HIGH (Instant Stop, Choppy)[/bold green]" if HIGH_PRECISION_JOG else "[bold yellow]LOW (Smooth Glide, Slight Coast)[/bold yellow]"

    console.print(Panel(
        f"[bold cyan]Headless SSH Jog Menu[/bold cyan]\n"
        f"Precision Mode: {mode_str}\n\n"
        f"Press or hold keys to move the printer. Commands are sent at F{JOG_SPEED_MM_MIN} in {JOG_DISTANCE}mm chunks.\n"
        "[dim](Note: Diagonal movement is limited over SSH, but rapid key-tapping works!)[/dim]\n\n"
        " [bold yellow]W[/bold yellow] : +Y    [bold yellow]S[/bold yellow] : -Y\n"
        " [bold yellow]A[/bold yellow] : -X    [bold yellow]D[/bold yellow] : +X\n"
        " [bold yellow]R[/bold yellow] : +Z    [bold yellow]F[/bold yellow] : -Z\n"
        " [bold yellow]T[/bold yellow] : -B    [bold yellow]G[/bold yellow] : +B\n\n"
        "Press [bold magenta]'p'[/bold magenta] to swap between High and Low Precision.\n"
        "Press [bold red]'q'[/bold red] to return to the main menu.",
        border_style="cyan"
    ))

    printer_conn.reset_input_buffer()
    printer_conn.write(b"G91\n")  # Switch to Relative Mode

    is_windows = sys.platform == 'win32'
    if not is_windows:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
        except Exception:
            pass

    toggle_requested = False
    in_flight_commands = 0
    last_command_time = time.time()

    try:
        while True:
            if in_flight_commands > 0 and (time.time() - last_command_time) > 0.5:
                in_flight_commands = 0
                printer_conn.reset_input_buffer()

            while printer_conn.in_waiting > 0:
                try:
                    resp = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                    if 'ok' in resp.lower():
                        in_flight_commands = max(0, in_flight_commands - 1)
                except Exception:
                    pass

            char = None
            if is_windows:
                if msvcrt.kbhit():
                    char = msvcrt.getch().decode('utf-8', errors='ignore').lower()
            else:
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    char = sys.stdin.read(1).lower()

            if char:
                if char == 'q':
                    break
                elif char == 'p':
                    HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
                    toggle_requested = True
                    break

                limit = 0 if HIGH_PRECISION_JOG else 2

                if in_flight_commands <= limit:
                    dx, dy, dz, de = 0.0, 0.0, 0.0, 0.0

                    if char == 'w':   dy += JOG_DISTANCE
                    elif char == 's': dy -= JOG_DISTANCE
                    elif char == 'a': dx -= JOG_DISTANCE
                    elif char == 'd': dx += JOG_DISTANCE
                    elif char == 'r': dz += JOG_DISTANCE
                    elif char == 'f': dz -= JOG_DISTANCE
                    elif char == 't': de -= JOG_DISTANCE
                    elif char == 'g': de += JOG_DISTANCE

                    if dx != 0 or dy != 0 or dz != 0 or de != 0:
                        cmd = "G1"
                        if dx != 0: cmd += f" X{dx:.2f}"
                        if dy != 0: cmd += f" Y{dy:.2f}"
                        if dz != 0: cmd += f" Z{dz:.2f}"
                        if de != 0: cmd += f" {EXTRUSION_AXIS}{de:.2f}"
                        cmd += f" F{JOG_SPEED_MM_MIN}\n"

                        if HIGH_PRECISION_JOG:
                            printer_conn.write((cmd + "M400\n").encode('utf-8'))
                            in_flight_commands += 2
                        else:
                            printer_conn.write(cmd.encode('utf-8'))
                            in_flight_commands += 1

                        last_command_time = time.time()

    finally:
        if not is_windows:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
        printer_conn.write(b"G90\n")  # Return to Absolute Mode

    if toggle_requested:
        return "reload"
    return "quit"


# ============================================================
# --- MANUAL G-CODE TERMINAL ---
# ============================================================

def manual_control_menu():
    global printer_conn

    if not printer_conn:
        console.print("[bold red]Printer not connected! Please connect first.[/bold red]")
        time.sleep(1.5)
        return

    console.clear()
    display_header()
    console.print(Panel(
        "[bold cyan]Manual G-Code Terminal[/bold cyan]\n"
        "Type your G-Code commands and press Enter.\n"
        "Movement commands (G0/G1) default to F300 if no speed is specified.\n\n"
        "[bold yellow]TIP:[/bold yellow] If movements like 'G1 X0.2' aren't doing anything, the printer is likely in Absolute Mode.\n"
        "Send [bold green]G91[/bold green] to switch to Relative Mode, then try your move again.\n\n"
        "Type [bold yellow]'q'[/bold yellow] or [bold yellow]'quit'[/bold yellow] to return to the main menu.",
        border_style="cyan"
    ))

    printer_conn.reset_input_buffer()

    while True:
        cmd = Prompt.ask("[bold green]>[/bold green]")

        if cmd.lower() in ['q', 'quit', 'exit']:
            break

        if not cmd.strip():
            continue

        cmd_clean = re.sub(r'[–—−]', '-', cmd)
        cmd_clean = re.sub(r'([A-Z])\s+([-\.0-9])', r'\1\2', cmd_clean, flags=re.IGNORECASE)
        cmd_upper = cmd_clean.upper().strip()

        if cmd_upper.startswith("G0") or cmd_upper.startswith("G1"):
            if "F" not in cmd_upper:
                cmd_upper += " F300"

        try:
            printer_conn.write((cmd_upper + '\n').encode('ascii', errors='ignore'))

            response_lines = []
            start_wait = time.time()

            while True:
                if time.time() - start_wait > 5.0:
                    console.print("[dim yellow]Warning: Printer didn't respond with 'ok' within 5 seconds.[/dim yellow]")
                    break

                response = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                if response:
                    response_lines.append(response)
                    if 'ok' in response.lower():
                        break
                else:
                    break

            for r in response_lines:
                console.print(f"[dim]{r}[/dim]")

        except serial.SerialException as e:
            console.print(f"[bold red]Serial connection error: {e}[/bold red]")
            break


# ============================================================
# --- G-CODE TRANSLATION ---
# ============================================================

def translate_gcode():
    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)
        console.print(Panel(f"[bold yellow]Created '{raw_dir}' directory.[/bold yellow]\n\nPlease place your raw files there.", title="[bold red]Action Required"))
        time.sleep(2)
        return

    os.makedirs(out_dir, exist_ok=True)

    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(raw_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(f"[bold red]No files found in '{raw_dir}'.[/bold red]"))
        time.sleep(2)
        return

    files.sort(key=lambda x: os.path.getmtime(os.path.join(raw_dir, x)), reverse=True)

    file_table = Table(show_header=True, header_style="bold green", title="[bold cyan]Available Files in 'raw_gcode'")
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")

    for i, f in enumerate(files):
        mtime = os.path.getmtime(os.path.join(raw_dir, f))
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str)

    console.print(file_table)
    console.print(f"[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select a file to translate[/bold yellow]", choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0: return

    selected_file = files[choice - 1]
    input_filepath = os.path.join(raw_dir, selected_file)

    base_name, ext = os.path.splitext(selected_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{base_name}_{timestamp}{ext}"
    output_filepath = os.path.join(out_dir, output_filename)

    try:
        with open(input_filepath, "r") as file:
            content = file.readlines()
    except FileNotFoundError:
        console.print(f"[bold red]Error: '{input_filepath}' not found.[/bold red]")
        time.sleep(2)
        return

    coordinate_type = 0 if COORDINATE_MODE == "G90" else 1
    extrusion_coefficient = EXTRUSION_COEFFICIENT
    extruder = 0
    netExtrude = 0
    netExtrude_A = 0

    console.print(f"\n[bold green]Translating[/bold green] [cyan]'{selected_file}'[/cyan] -> [cyan]'{output_filename}'[/cyan]...\n")

    f_new = open(output_filepath, "w+t")
    f_new.write(COORDINATE_MODE + "\n")

    f_new.write("; --- Initialization Sequence ---\n")
    f_new.write("G90 ; Force absolute positioning for setup\n")

    if START_FROM_CENTER:
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Zero all axes at the current center position\n")
    else:
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Zero at confirmed bottom-left corner\n")
        f_new.write("G1 Z30 F300 ; Z-hop up 30mm to clear dish walls\n")
        f_new.write("G1 X50 Y50 F300 ; Move to the center\n")
        f_new.write("G1 Z0 F300 ; Drop back down to original height before printing\n")
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Re-zero all axes at the center\n")

    if COORDINATE_MODE == "G91":
        f_new.write("G91 ; Restore relative positioning\n")
    f_new.write("; ----------------------------------------\n\n")

    if DO_AUTO_PRESSURIZE:
        f_new.write("; Auto-pressurize syringe\n")
        f_new.write("G91 ; Switch to relative positioning for pressurize\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")
        if COORDINATE_MODE == "G90":
            f_new.write("G90 ; Switch back to absolute positioning\n")
        f_new.write(f"G92 {EXTRUSION_AXIS}0 ; Re-zero the extrusion axis after pressurizing\n\n")

    x1, y1, e1, a1, z1 = 0.0, 0.0, 0.0, 0.0, 0.0
    e1_orig = 0.0

    with Progress(
        SpinnerColumn(spinner_name="monkey"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="magenta", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:

        task = progress.add_task("[cyan]Processing G-Code...", total=len(content))

        for line in content:
            original_line = line
            stripped_line = line.strip()

            if stripped_line.startswith('M'):
                if not (stripped_line.startswith('M106') or stripped_line.startswith('M107')):
                    progress.advance(task)
                    continue

            if "syringe_diameter" in stripped_line or "nozzle_diameter" in stripped_line or "extrusion_coefficient" in stripped_line:
                progress.advance(task)
                continue

            if 'G92 E0' in stripped_line or f'G92 {EXTRUSION_AXIS}0' in stripped_line:
                e1 = 0.0
                e1_orig = 0.0

            if not stripped_line or stripped_line.startswith(';') or 'G90' in stripped_line or 'G91' in stripped_line or 'G92' in stripped_line or 'G21' in stripped_line or 'G4' in stripped_line:
                if ('G90' in stripped_line or 'G91' in stripped_line) and "G9" in original_line[:3]:
                    progress.advance(task)
                    continue

                if 'G92' in stripped_line and 'E' in stripped_line:
                    f_new.write(original_line.replace('E', EXTRUSION_AXIS))
                else:
                    f_new.write(original_line)

                progress.advance(task)
                continue

            if 'T0' in stripped_line:
                f_new.write('T0\n')
                extruder = 0
                progress.advance(task)
                continue
            if 'T1' in stripped_line:
                f_new.write('T1\n')
                extruder = 1
                progress.advance(task)
                continue

            if stripped_line.startswith('K') or stripped_line.startswith('k'):
                new_k = stripped_line.split('=')
                try:
                    extrusion_coefficient = float(new_k[-1].strip())
                    f_new.write(f"; extrusion coefficient changed to = {extrusion_coefficient}\n")
                except ValueError:
                    pass
                progress.advance(task)
                continue

            if stripped_line.startswith('B') or stripped_line.startswith('b') or stripped_line.startswith('C') or stripped_line.startswith('c'):
                progress.advance(task)
                continue

            letters = {'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None, 'I': None, 'J': None, 'R': None, 'T': None, 'E': None, 'F': None}
            var = False
            for command in stripped_line.split():
                if command.startswith(';'): break
                if command.endswith(';'):
                    command = command[:-1]
                    var = True
                if command[0].upper() in letters:
                    try:
                        letters[command[0].upper()] = float(command[1:])
                    except ValueError:
                        pass
                if var: break

            motion_axes = ['X', 'Y', 'Z', 'A', 'I', 'J', 'R', 'T']
            if not any(letters.get(c) is not None for c in motion_axes):
                f_new.write(original_line)
                progress.advance(task)
                continue

            g = letters.get('G')
            x = letters.get('X')
            y = letters.get('Y')
            z = letters.get('Z')
            a = letters.get('A')
            i = letters.get('I')
            j = letters.get('J')
            r = letters.get('R')
            f = letters.get('F')

            l = 0

            x_val = x if x is not None else (x1 if coordinate_type == 0 else 0)
            y_val = y if y is not None else (y1 if coordinate_type == 0 else 0)
            z_val = z if z is not None else (z1 if coordinate_type == 0 else 0)
            a_val = a if a is not None else (a1 if coordinate_type == 0 else 0)
            i_val = i if i is not None else 0
            j_val = j if j is not None else 0

            if coordinate_type == 0:
                x_rel = (x_val - x1) if x is not None else 0
                y_rel = (y_val - y1) if y is not None else 0
                z_rel = (z_val - z1) if z is not None else 0
                a_rel = (a_val - a1) if a is not None else 0
            else:
                x_rel = x if x is not None else 0
                y_rel = y if y is not None else 0
                z_rel = z if z is not None else 0
                a_rel = a if a is not None else 0

            if g == 1:
                l = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
            elif g == 2 or g == 3:
                full_circle = False
                radius = r
                if radius is None:
                    radius = math.sqrt(i_val**2 + j_val**2)

                if x_rel != 0 or y_rel != 0 or z_rel != 0 or a_rel != 0:
                    d = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                    if radius > 0:
                        val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                        theta = 2 * math.pi - math.acos(val)
                    else:
                        theta = 0
                else:
                    theta = 2 * math.pi
                    full_circle = True

                l = radius * theta
                if g == 3 and not full_circle:
                    l = 2 * math.pi * radius - l

            original_e = letters.get('E')

            if original_e is None:
                chunk = 0
            else:
                if coordinate_type == 1:
                    e_change = original_e
                else:
                    e_change = original_e - e1_orig

                if e_change == 0:
                    chunk = 0
                else:
                    if l > 0:
                        if extruder == 0:
                            chunk = (extrusion_coefficient * l * Z_NOZZLE_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                        else:
                            chunk = (extrusion_coefficient * l * A_NOZZLE_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)
                        if e_change < 0:
                            chunk = -chunk
                    else:
                        FILAMENT_DIAMETER = 1.75
                        if extruder == 0:
                            chunk = e_change * (FILAMENT_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                        else:
                            chunk = e_change * (FILAMENT_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)

            if original_e is not None:
                if coordinate_type == 1:
                    e = chunk
                else:
                    e = e1 + chunk
                if extruder == 0:
                    netExtrude += chunk
                else:
                    netExtrude_A += chunk
                e1_orig = original_e
            else:
                e = None

            write_line = ""
            if g is not None: write_line += 'G' + str(int(g))
            if x is not None: write_line += ' X' + str(x)
            if y is not None: write_line += ' Y' + str(y)
            if g in (2, 3):
                if r is not None: write_line += ' R' + str(r)
                if i is not None: write_line += ' I' + str(i)
                if j is not None: write_line += ' J' + str(j)
            if z is not None: write_line += ' Z' + str(z)
            if a is not None: write_line += ' A' + str(a)
            if e is not None and g != 0: write_line += f' {EXTRUSION_AXIS}' + str(round(e, 3))
            if f is not None: write_line += ' F' + str(f)

            if 'NO E' in original_line:
                f_new.write(original_line)
                if original_e is not None:
                    if coordinate_type == 0:
                        e -= chunk
                    if extruder == 0:
                        netExtrude -= chunk
                    else:
                        netExtrude_A -= chunk
            else:
                f_new.write(write_line + "\n")

            if coordinate_type == 0:
                x1 = x_val if x is not None else x1
                y1 = y_val if y is not None else y1
                z1 = z_val if z is not None else z1
                a1 = a_val if a is not None else a1
            else:
                if x is not None: x1 += x
                if y is not None: y1 += y
                if z is not None: z1 += z
                if a is not None: a1 += a

            e1 = e if e is not None else e1
            progress.advance(task)

    if DO_AUTO_PRESSURIZE:
        f_new.write(f"\n; Auto-depressurize syringe\n")
        f_new.write("G91 ; Switch to relative positioning for depressurize\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{-PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")
        if COORDINATE_MODE == "G90":
            f_new.write("G90 ; Switch back to absolute positioning\n")

    f_new.write("\n; --- End of Print Sequence ---\n")
    f_new.write("G91 ; Switch to relative positioning\n")
    f_new.write("G1 Z30 F300 ; Lift nozzle 30mm to safely clear the print\n")
    f_new.write("G90 ; Switch back to absolute positioning\n")
    if START_FROM_CENTER:
        f_new.write("G1 X0 Y0 F300 ; Park the bed back at the center\n")
    else:
        f_new.write("G1 X-50 Y-50 F300 ; Park the bed back at the original bottom-left edge\n")
    f_new.write("; -----------------------------\n")

    f_new.close()

    netVol_Z = netExtrude   * math.pi * (Z_SYRINGE_DIAMETER / 2)**2 / 1000
    netVol_A = netExtrude_A * math.pi * (A_SYRINGE_DIAMETER / 2)**2 / 1000

    success_text = (
        f"[bold cyan]Extruder B (Z syringe):[/bold cyan]\n"
        f"  Distance: [bold yellow]{round(netExtrude, 3)} mm[/bold yellow]   "
        f"Volume: [bold yellow]{round(netVol_Z, 3)} mL[/bold yellow]\n\n"
        f"[bold cyan]Extruder C (A syringe):[/bold cyan]\n"
        f"  Distance: [bold yellow]{round(netExtrude_A, 3)} mm[/bold yellow]   "
        f"Volume: [bold yellow]{round(netVol_A, 3)} mL[/bold yellow]"
    )
    console.print()
    console.print(Panel(success_text, title="[bold green]Translation Complete[/bold green]", border_style="green", expand=False))

    load_now = Prompt.ask("\nLoad this file for printing now?", choices=["y", "n"], default="y")
    if load_now.lower() == 'y':
        global loaded_filepath
        loaded_filepath = output_filepath
        console.print(f"[bold green]Loaded {output_filename}![/bold green]")
        time.sleep(1)


# ============================================================
# --- PRINT CONTROLS ---
# ============================================================

def check_for_pause(progress):
    pause_requested = False

    if sys.platform == 'win32':
        if msvcrt.kbhit():
            msvcrt.getch()
            pause_requested = True
    else:
        if sys.stdin in select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.readline()
            pause_requested = True

    if pause_requested:
        try:
            printer_conn.write(b"M220 S0\n")
        except serial.SerialException:
            pass

        progress.stop()
        console.print("\n[bold yellow]PRINT PAUSED[/bold yellow]")

        action = Prompt.ask(
            "[bold cyan]Choose an action:[/bold cyan] [bold green](r)esume[/bold green] or [bold red](s)top[/bold red]",
            choices=["r", "s"],
            default="r"
        )

        if action == 's':
            console.print("[bold red]Cancelling print and parking bed...[/bold red]")
            try:
                printer_conn.write(b"M410\n")
                time.sleep(0.5)
                printer_conn.reset_input_buffer()
                printer_conn.write(b"M220 S100\n")
                printer_conn.write(b"G91\n")
                printer_conn.write(b"G1 Z30 F300\n")
                printer_conn.write(b"G90\n")

                if START_FROM_CENTER:
                    printer_conn.write(b"G1 X0 Y0 F300\n")
                else:
                    printer_conn.write(b"G1 X-50 Y-50 F300\n")
            except Exception as e:
                console.print(f"[dim]Failed to send park command: {e}[/dim]")
            return True
        else:
            console.print("[bold green]Resuming print...[/bold green]")
            try:
                printer_conn.write(b"M220 S100\n")
            except serial.SerialException:
                pass
            progress.start()
            return False

    return False


def load_file_menu():
    global loaded_filepath

    out_dir = "translated_gcode"

    if not os.path.exists(out_dir):
        console.print(Panel(
            f"[bold red]No '{out_dir}' directory found.[/bold red]\n\n"
            "Translate a file first (option 2) to create it.",
            border_style="red"
        ))
        time.sleep(2)
        return

    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(out_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(
            f"[bold red]No translated files found in '{out_dir}'.[/bold red]\n\n"
            "Translate a file first (option 2).",
            border_style="red"
        ))
        time.sleep(2)
        return

    files.sort(key=lambda x: os.path.getmtime(os.path.join(out_dir, x)), reverse=True)

    file_table = Table(
        show_header=True,
        header_style="bold green",
        title=f"[bold cyan]Translated Files in '{out_dir}'[/bold cyan]"
    )
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")
    file_table.add_column("Size", justify="right", style="yellow")

    for i, f in enumerate(files):
        full_path = os.path.join(out_dir, f)
        mtime = os.path.getmtime(full_path)
        size_kb = os.path.getsize(full_path) / 1024
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str, f"{size_kb:.1f} KB")

    console.print(file_table)

    if loaded_filepath:
        console.print(f"\nCurrently loaded: [bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]")

    console.print("[0] Cancel\n")

    choice = IntPrompt.ask(
        "[bold yellow]Select a file to load[/bold yellow]",
        choices=[str(i) for i in range(len(files) + 1)]
    )

    if choice == 0:
        return

    selected_file = files[choice - 1]
    loaded_filepath = os.path.join(out_dir, selected_file)
    console.print(f"\n[bold green]Loaded:[/bold green] [cyan]{selected_file}[/cyan]")
    time.sleep(1.5)


def print_file():
    global printer_conn, loaded_filepath

    if not printer_conn:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1)
        return

    if not loaded_filepath:
        console.print("[bold red]No file loaded![/bold red]")
        time.sleep(1)
        return

    console.print()

    if START_FROM_CENTER:
        warning_text = "ACTION REQUIRED: Please move the bed to the CENTER before continuing."
        prompt_text = "Is the bed in the center position?"
    else:
        warning_text = "ACTION REQUIRED: Please move the bed to the far bottom left corner before continuing."
        prompt_text = "Is the bed in the bottom left position?"

    console.print(Panel(f"[bold yellow]{warning_text}[/bold yellow]", border_style="yellow"))
    ready = Prompt.ask(prompt_text, choices=["y", "n"], default="y")

    if ready.lower() != 'y':
        console.print("[bold red]Print cancelled.[/bold red]")
        time.sleep(1.5)
        return

    try:
        with open(loaded_filepath, "r") as file:
            lines = file.readlines()
    except Exception as e:
        console.print(f"[bold red]Error reading file: {e}[/bold red]")
        time.sleep(2)
        return

    console.print(Panel(
        f"[bold yellow]Starting print: {os.path.basename(loaded_filepath)}[/bold yellow]\n"
        f"[bold cyan]Press ENTER to PAUSE the print.[/bold cyan]"
    ))

    printer_conn.reset_input_buffer()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:

        task = progress.add_task("[cyan]Printing...", total=len(lines))

        i = 0
        command_sent = False
        print_aborted = False

        while i < len(lines):
            if check_for_pause(progress):
                print_aborted = True
                break

            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith(';'):
                i += 1
                progress.advance(task)
                continue

            command = stripped.split(';')[0].strip()

            if command:
                if not command_sent:
                    printer_conn.write((command + '\n').encode('utf-8'))
                    command_sent = True

                waiting_for_ok = True
                while waiting_for_ok:
                    if check_for_pause(progress):
                        print_aborted = True
                        break

                    if printer_conn.in_waiting > 0:
                        try:
                            response = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                            if 'ok' in response.lower():
                                waiting_for_ok = False
                        except serial.SerialException:
                            console.print("[bold red]Serial connection lost during print![/bold red]")
                            return

                    time.sleep(0.01)

            if print_aborted:
                break

            i += 1
            command_sent = False
            progress.advance(task)

        if not print_aborted and i >= len(lines):
            progress.update(task, description="[cyan]Finishing buffered moves in printer hardware...")
            try:
                printer_conn.write(b"M400\n")
                waiting_for_ok = True
                while waiting_for_ok:
                    if check_for_pause(progress):
                        print_aborted = True
                        break

                    if printer_conn.in_waiting > 0:
                        try:
                            response = printer_conn.readline().decode('utf-8', errors='ignore').strip()
                            if 'ok' in response.lower():
                                waiting_for_ok = False
                        except serial.SerialException:
                            break
                    time.sleep(0.01)
            except Exception:
                pass

    if not print_aborted:
        console.print("\n[bold green]Print completed successfully![/bold green]")
        time.sleep(2)
    else:
        time.sleep(2)


# ============================================================
# --- ENDSTOP DIAGNOSTIC TEST ---
# ============================================================

def endstop_test_menu():
    """
    Continuously polls M119 and prints which endstop is triggered.
    Useful for diagnosing sensorless homing — send G28 from the manual
    terminal while this is running to watch the virtual endstops fire.
    """
    if not printer_conn or not printer_conn.is_open:
        console.print("[bold red]Printer not connected! Please connect first.[/bold red]")
        time.sleep(1.5)
        return

    console.clear()
    display_header()
    console.print(Panel(
        "[bold cyan]Endstop Status Monitor (M119)[/bold cyan]\n\n"
        "Polls M119 continuously. With sensorless firmware (v1-v5),\n"
        "the virtual endstops fire when StallGuard detects a stall.\n\n"
        "  [bold yellow]x_min / x_max[/bold yellow] → X axis StallGuard\n"
        "  [bold yellow]y_min / y_max[/bold yellow] → Y axis StallGuard\n"
        "  [bold yellow]z_min / z_max[/bold yellow] → Z axis StallGuard\n\n"
        "Type [bold red]'q'[/bold red] and press Enter to return to the main menu.",
        border_style="cyan"
    ))

    printer_conn.reset_input_buffer()

    ENDSTOP_LABELS = {
        "x_min": "X axis — MIN end",
        "x_max": "X axis — MAX end",
        "y_min": "Y axis — MIN end",
        "y_max": "Y axis — MAX end",
        "z_min": "Z axis — MIN end",
        "z_max": "Z axis — MAX end",
        "a_min": "A axis — MIN end",
        "b_min": "B axis — MIN end",
    }

    is_windows = sys.platform == 'win32'
    if not is_windows:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
        except Exception:
            pass

    try:
        while True:
            char = None
            if is_windows:
                if msvcrt.kbhit():
                    char = msvcrt.getch().decode('utf-8', errors='ignore').lower()
            else:
                if select.select([sys.stdin], [], [], 0)[0]:
                    char = sys.stdin.read(1).lower()

            if char == 'q':
                break

            printer_conn.write(b"M119\n")

            raw_lines = []
            start = time.time()
            while True:
                if time.time() - start > 3.0:
                    break
                if printer_conn.in_waiting > 0:
                    try:
                        line = printer_conn.readline().decode('utf-8', errors='ignore').strip().lower()
                        if line:
                            raw_lines.append(line)
                        if 'ok' in line:
                            break
                    except Exception:
                        break
                time.sleep(0.01)

            any_triggered = False
            for line in raw_lines:
                for key, label in ENDSTOP_LABELS.items():
                    if key in line and 'triggered' in line:
                        console.print(f"[bold green][{label}][/bold green] [bold white on green] TRIGGERED [/bold white on green]")
                        any_triggered = True

            if not any_triggered:
                console.print("[dim]All endstops open — waiting...[/dim]", end="\r")

            time.sleep(0.3)

    finally:
        if not is_windows:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass


# ============================================================
# --- GITHUB UPDATE ---
# ============================================================

def update_orca():
    global printer_conn
    console.print(Panel("[bold cyan]Fetching latest updates from GitHub...[/bold cyan]", border_style="cyan"))
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        console.print("[bold green]Successfully pulled latest changes![/bold green]")
        if result.stdout.strip():
            console.print(f"[dim]{result.stdout.strip()}[/dim]")

        if "Already up to date." in result.stdout:
            time.sleep(2)
            return

        console.print("\n[bold yellow]Restarting ORCA to apply updates...[/bold yellow]")
        time.sleep(2)

        if printer_conn:
            try:
                printer_conn.close()
            except Exception:
                pass
            printer_conn = None

        os.execl(sys.executable, sys.executable, *sys.argv)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]Failed to update from GitHub.[/bold red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        time.sleep(3)
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred: {e}[/bold red]")
        time.sleep(3)


# ============================================================
# --- MAIN MENU ---
# ============================================================

def main():
    while True:
        console.clear()
        display_header()

        conn_status = f"[bold green]Connected ({printer_conn.port})[/bold green]" if printer_conn else "[bold red]Not Connected[/bold red]"
        console.print(f"Printer Status: {conn_status}")

        file_status = f"[bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]" if loaded_filepath else "[dim]None[/dim]"
        console.print(f"Loaded File:    {file_status}\n")

        console.print("[bold yellow]--- Main Menu ---[/bold yellow]")

        valid_choices = ["1", "2", "3", "9", "10", "11"]

        if printer_conn:
            console.print("[0] [bold red]Reset / Reboot Printer Board[/bold red]")
            valid_choices.append("0")

        console.print("[1] Connect to Printer")
        console.print("[2] Translate G-Code")
        console.print("[3] Load Translated File")

        if printer_conn and loaded_filepath:
            console.print("[4] [bold green]Print Loaded File[/bold green]")
            valid_choices.append("4")
        else:
            console.print("[4] [dim]Print Loaded File (Requires Connection & File)[/dim]")

        if printer_conn:
            console.print("[5] [bold cyan]Manual G-Code Terminal[/bold cyan]")
            jog_label = "[bold cyan]Jog Control[/bold cyan]" if PYNPUT_AVAILABLE else "[dim]Jog Control (unavailable headless)[/dim]"
            console.print(f"[6] {jog_label}")
            console.print("[7] [bold cyan]Endstop Status Monitor[/bold cyan]")
            console.print("[8] [bold magenta]Sensorless Home (StallGuard)[/bold magenta]")
            valid_choices.extend(["5", "6", "7", "8"])
        else:
            console.print("[5] [dim]Manual G-Code Terminal (Requires Connection)[/dim]")
            console.print("[6] [dim]Jog Control (Requires Connection)[/dim]")
            console.print("[7] [dim]Endstop Status Monitor (Requires Connection)[/dim]")
            console.print("[8] [dim]Sensorless Home (Requires Connection)[/dim]")

        console.print("[9] Options / Settings")
        console.print("[10] Update ORCA from GitHub")
        console.print("[11] Exit\n")

        valid_choices = sorted(set(valid_choices))
        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=valid_choices)

        if choice == "0":
            reset_printer_board()
        elif choice == "1":
            connect_to_printer()
        elif choice == "2":
            translate_gcode()
        elif choice == "3":
            load_file_menu()
        elif choice == "4":
            print_file()
        elif choice == "5":
            manual_control_menu()
        elif choice == "6":
            while True:
                res = interactive_jog_menu()
                if res != "reload":
                    break
        elif choice == "7":
            endstop_test_menu()
        elif choice == "8":
            sensorless_home_menu()
        elif choice == "9":
            settings_menu()
        elif choice == "10":
            update_orca()
        elif choice == "11":
            if printer_conn:
                try:
                    printer_conn.close()
                except Exception:
                    pass
            console.print("[bold magenta]Goodbye![/bold magenta]")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if printer_conn:
            try:
                printer_conn.close()
            except Exception:
                pass
        console.print("\n[bold magenta]Goodbye![/bold magenta]")
