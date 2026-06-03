# NOTE:
# This file preserves your G-code translation logic while replacing
# the serial communication / print streaming system with a far more
# reliable implementation for macOS + Marlin/Reprap firmware.

import math
import os
import subprocess
import time
import sys
import re
import threading
import queue
from datetime import datetime

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
    print("Please install the 'pyserial' library: pip install pyserial==3.5")
    sys.exit()

# Initialize the Rich Console
console = Console()

# ==========================================
# --- CONFIGURATION PARAMETERS ---
# ==========================================
COORDINATE_MODE = "G90"
EXTRUSION_AXIS = "B"

Z_SYRINGE_DIAMETER = 4.9
A_SYRINGE_DIAMETER = 4.9

Z_NOZZLE_DIAMETER = 2
A_NOZZLE_DIAMETER = 0.2

EXTRUSION_COEFFICIENT = 0.33

DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 0.2
PRESSURIZE_SPEED = 300

BAUD_RATE = 115200

# ==========================================
# --- STATE VARIABLES ---
# ==========================================
printer_conn = None
loaded_filepath = None

printer_lock = threading.Lock()
printer_response_queue = queue.Queue()

printer_listener_running = False
printer_listener_thread = None


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
    console.print("                    [cyan]v2.0.0[/cyan]\n")


# ============================================================
# --- SERIAL LISTENER THREAD ---
# ============================================================

def serial_listener():
    global printer_listener_running
    global printer_conn

    while printer_listener_running:

        try:

            if printer_conn and printer_conn.is_open:

                line = printer_conn.readline()

                if line:

                    decoded = line.decode(
                        'utf-8',
                        errors='ignore'
                    ).strip()

                    if decoded:
                        printer_response_queue.put(decoded)

            else:
                time.sleep(0.05)

        except serial.SerialException:
            time.sleep(0.1)

        except Exception:
            time.sleep(0.1)


# ============================================================
# --- SAFE COMMAND SENDER ---
# ============================================================

def send_gcode(command, timeout=15, retries=3, wait_for_ok=True):

    global printer_conn

    if not printer_conn or not printer_conn.is_open:
        raise RuntimeError("Printer not connected")

    command = command.strip()

    if not command:
        return True

    for attempt in range(retries):

        try:

            with printer_lock:
                printer_conn.write(
                    (command + '\n').encode('utf-8')
                )
                printer_conn.flush()

            if not wait_for_ok:
                return True

            start = time.time()

            while time.time() - start < timeout:

                try:
                    response = printer_response_queue.get(timeout=0.25)

                except queue.Empty:
                    continue

                response_lower = response.lower()

                console.print(f"[dim]{response}[/dim]")

                if response_lower.startswith("ok"):
                    return True

                if "error" in response_lower:
                    console.print(
                        f"[bold red]{response}[/bold red]"
                    )
                    break

                if "resend" in response_lower:
                    break

                if response_lower.startswith("busy"):
                    start = time.time()
                    continue

            console.print(
                f"[yellow]Retrying:[/yellow] {command}"
            )

        except serial.SerialTimeoutException:
            console.print(
                "[yellow]Serial timeout, retrying...[/yellow]"
            )

        except serial.SerialException as e:

            if "temporarily unavailable" in str(e).lower():
                time.sleep(0.1)
                continue

            raise e

        time.sleep(0.25)

    raise RuntimeError(
        f"Failed command after retries: {command}"
    )


# ============================================================
# --- CONNECT TO PRINTER ---
# ============================================================

def connect_to_printer():

    global printer_conn
    global printer_listener_running
    global printer_listener_thread

    if printer_conn and printer_conn.is_open:

        try:
            printer_listener_running = False
            printer_conn.close()

        except Exception:
            pass

    ports = serial.tools.list_ports.comports()

    if not ports:
        console.print(
            "[bold red]No serial ports found.[/bold red]"
        )
        time.sleep(2)
        return

    console.print("[bold cyan]Available Ports:[/bold cyan]")

    for i, port in enumerate(ports):

        console.print(
            f"[{i + 1}] {port.device} - {port.description}"
        )

    console.print("[0] Cancel")

    choice = IntPrompt.ask(
        "\n[bold yellow]Select the port[/bold yellow]",
        choices=[str(i) for i in range(len(ports) + 1)]
    )

    if choice == 0:
        return

    selected_port = ports[choice - 1].device

    try:

        with console.status(
            f"[bold green]Connecting to "
            f"{selected_port}...",
            spinner="dots"
        ):

            printer_conn = serial.Serial(
                selected_port,
                BAUD_RATE,
                timeout=1,
                write_timeout=5,
                exclusive=True if sys.platform == "darwin" else None
            )

            # HARD RESET
            printer_conn.dtr = False
            time.sleep(1.0)

            printer_conn.reset_input_buffer()
            printer_conn.reset_output_buffer()

            printer_conn.dtr = True

            # WAIT FOR BOARD BOOT
            time.sleep(4)

            printer_conn.reset_input_buffer()

            printer_listener_running = True

            printer_listener_thread = threading.Thread(
                target=serial_listener,
                daemon=True
            )

            printer_listener_thread.start()

            # WAKE PRINTER
            send_gcode("M115", timeout=10)

            console.print(
                f"[bold green]Connected to "
                f"{selected_port}![/bold green]"
            )

            time.sleep(1)

    except Exception as e:

        console.print(
            f"[bold red]Failed to connect:[/bold red] {e}"
        )

        printer_conn = None
        time.sleep(2)


# ============================================================
# --- RESET PRINTER ---
# ============================================================

def reset_printer_board():

    global printer_conn

    if not printer_conn:

        console.print(
            "[bold red]Printer not connected![/bold red]"
        )

        time.sleep(1.5)
        return

    console.print(
        "[bold yellow]Resetting printer...[/bold yellow]"
    )

    try:

        send_gcode("M112", wait_for_ok=False)
        time.sleep(0.5)

        printer_conn.dtr = False
        time.sleep(1.0)

        printer_conn.dtr = True

        time.sleep(4)

        printer_conn.reset_input_buffer()
        printer_conn.reset_output_buffer()

        console.print(
            "[bold green]Printer reset complete.[/bold green]"
        )

        time.sleep(2)

    except Exception as e:

        console.print(
            f"[bold red]Reset failed:[/bold red] {e}"
        )

        time.sleep(2)


# ============================================================
# --- MANUAL TERMINAL ---
# ============================================================

def manual_control_menu():

    global printer_conn

    if not printer_conn:

        console.print(
            "[bold red]Printer not connected![/bold red]"
        )

        time.sleep(1.5)
        return

    console.clear()
    display_header()

    console.print(
        Panel(
            "[bold cyan]Manual G-Code Terminal[/bold cyan]\n"
            "Type G-code commands.\n\n"
            "Type 'q' to exit.",
            border_style="cyan"
        )
    )

    while True:

        cmd = Prompt.ask("[bold green]>[/bold green]")

        if cmd.lower() in ['q', 'quit', 'exit']:
            break

        if not cmd.strip():
            continue

        cmd_clean = re.sub(r'[–—−]', '-', cmd)

        cmd_clean = re.sub(
            r'([A-Z])\s+([-\.0-9])',
            r'\1\2',
            cmd_clean,
            flags=re.IGNORECASE
        )

        cmd_upper = cmd_clean.upper().strip()

        if cmd_upper.startswith("G0") or cmd_upper.startswith("G1"):

            if "F" not in cmd_upper:
                cmd_upper += " F300"

        try:

            send_gcode(cmd_upper)

        except Exception as e:

            console.print(
                f"[bold red]Error:[/bold red] {e}"
            )


# ============================================================
# --- TRANSLATION SETTINGS ---
# ============================================================

def review_settings_before_translation(filename):

    global COORDINATE_MODE
    global EXTRUSION_COEFFICIENT
    global DO_AUTO_PRESSURIZE

    while True:

        console.clear()
        display_header()

        console.print(
            f"Preparing to translate: "
            f"[bold magenta]{filename}[/bold magenta]\n"
        )

        config_table = Table(
            show_header=True,
            header_style="bold yellow",
            expand=True,
            title="[bold cyan]Translation Settings[/bold cyan]"
        )

        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row(
            "Coordinate Mode",
            COORDINATE_MODE,
            "Extrusion Axis",
            EXTRUSION_AXIS
        )

        config_table.add_row(
            "Z Syringe (mm)",
            str(Z_SYRINGE_DIAMETER),
            "A Syringe (mm)",
            str(A_SYRINGE_DIAMETER)
        )

        config_table.add_row(
            "Z Nozzle (mm)",
            str(Z_NOZZLE_DIAMETER),
            "A Nozzle (mm)",
            str(A_NOZZLE_DIAMETER)
        )

        config_table.add_row(
            "Extrusion Coeff.",
            str(EXTRUSION_COEFFICIENT),
            "Auto-Pressurize",
            "[green]ON[/green]"
            if DO_AUTO_PRESSURIZE
            else "[red]OFF[/red]"
        )

        console.print(config_table)

        console.print(
            "\n[bold yellow]"
            "--- Pre-Translation Check ---"
            "[/bold yellow]"
        )

        console.print(
            "[1] [bold green]"
            "Proceed with Translation"
            "[/bold green]"
        )

        console.print("[2] Change Extrusion Coefficient")
        console.print("[3] Toggle Auto-Pressurize")
        console.print("[4] Toggle Coordinate Mode")
        console.print("[5] Cancel\n")

        choice = Prompt.ask(
            "[bold yellow]Choose an option[/bold yellow]",
            choices=["1", "2", "3", "4", "5"]
        )

        if choice == "1":
            return True

        elif choice == "2":

            new_coeff = Prompt.ask(
                "Enter new Extrusion Coefficient",
                default=str(EXTRUSION_COEFFICIENT)
            )

            try:
                EXTRUSION_COEFFICIENT = float(new_coeff)

            except ValueError:

                console.print(
                    "[bold red]Invalid number.[/bold red]"
                )

                time.sleep(1.5)

        elif choice == "3":
            DO_AUTO_PRESSURIZE = not DO_AUTO_PRESSURIZE

        elif choice == "4":

            COORDINATE_MODE = (
                "G91"
                if COORDINATE_MODE == "G90"
                else "G90"
            )

        elif choice == "5":
            return False


# ============================================================
# --- GCODE TRANSLATION ---
# ============================================================

# IMPORTANT:
# YOUR ORIGINAL TRANSLATION LOGIC IS PRESERVED.
# ONLY COMMUNICATION LAYER CHANGED.

def translate_gcode():

    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    if not os.path.exists(raw_dir):

        os.makedirs(raw_dir)

        console.print(
            Panel(
                f"[bold yellow]Created "
                f"'{raw_dir}' directory.[/bold yellow]",
                title="[bold red]Action Required"
            )
        )

        time.sleep(2)
        return

    os.makedirs(out_dir, exist_ok=True)

    valid_extensions = ('.gcode', '.txt')

    files = [
        f for f in os.listdir(raw_dir)
        if f.lower().endswith(valid_extensions)
    ]

    if not files:

        console.print(
            Panel(
                f"[bold red]No files found in "
                f"'{raw_dir}'.[/bold red]"
            )
        )

        time.sleep(2)
        return

    files.sort(
        key=lambda x:
        os.path.getmtime(os.path.join(raw_dir, x)),
        reverse=True
    )

    file_table = Table(
        show_header=True,
        header_style="bold green",
        title="[bold cyan]Available Files[/bold cyan]"
    )

    file_table.add_column("#")
    file_table.add_column("Filename")
    file_table.add_column("Modified")

    for i, f in enumerate(files):

        mtime = os.path.getmtime(
            os.path.join(raw_dir, f)
        )

        dt_str = datetime.fromtimestamp(
            mtime
        ).strftime('%Y-%m-%d %H:%M:%S')

        file_table.add_row(
            str(i + 1),
            f,
            dt_str
        )

    console.print(file_table)

    console.print("[0] Cancel")

    choice = IntPrompt.ask(
        "\n[bold yellow]Select a file[/bold yellow]",
        choices=[str(i) for i in range(len(files) + 1)]
    )

    if choice == 0:
        return

    selected_file = files[choice - 1]

    input_filepath = os.path.join(
        raw_dir,
        selected_file
    )

    proceed = review_settings_before_translation(
        selected_file
    )

    if not proceed:
        return

    base_name, ext = os.path.splitext(selected_file)

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output_filename = (
        f"{base_name}_{timestamp}{ext}"
    )

    output_filepath = os.path.join(
        out_dir,
        output_filename
    )

    try:

        with open(input_filepath, "r") as file:
            content = file.readlines()

    except FileNotFoundError:

        console.print(
            f"[bold red]Error: "
            f"'{input_filepath}' not found."
            f"[/bold red]"
        )

        time.sleep(2)
        return

    # ============================================================
    # ORIGINAL TRANSLATION LOGIC
    # ============================================================

    coordinate_type = 0 if COORDINATE_MODE == "G90" else 1
    extrusion_coefficient = EXTRUSION_COEFFICIENT

    extruder = 0

    netExtrude = 0
    netExtrude_A = 0

    console.print(
        f"\n[bold green]Translating[/bold green] "
        f"[cyan]'{selected_file}'[/cyan]"
    )

    f_new = open(output_filepath, "w+t")

    f_new.write(COORDINATE_MODE + "\n")

    f_new.write("; --- Initialization Sequence ---\n")
    f_new.write("G28 X Y Z\n")
    f_new.write("G91\n")
    f_new.write("G1 X50 Y67 Z-90 F800\n")
    f_new.write("G90\n")
    f_new.write(
        f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0\n"
    )

    if COORDINATE_MODE == "G91":
        f_new.write("G91\n")

    f_new.write("; --------------------------------\n\n")

    if DO_AUTO_PRESSURIZE:

        f_new.write("; Auto-pressurize syringe\n")
        f_new.write("G91\n")

        f_new.write(
            f"G1 {EXTRUSION_AXIS}"
            f"{PRESSURIZE_AMOUNT} "
            f"F{PRESSURIZE_SPEED}\n"
        )

        if COORDINATE_MODE == "G90":
            f_new.write("G90\n")

        f_new.write(
            f"G92 {EXTRUSION_AXIS}0\n\n"
        )

    # ============================================================
    # KEEPING YOUR ORIGINAL TRANSLATION CORE
    # ============================================================

    x1, y1, e1, a1, z1 = 0.0, 0.0, 0.0, 0.0, 0.0
    e1_orig = 0.0

    with Progress(
        SpinnerColumn(spinner_name="monkey"),
        TextColumn(
            "[progress.description]{task.description}"
        ),
        BarColumn(
            bar_width=40,
            style="magenta",
            complete_style="green"
        ),
        TextColumn(
            "[progress.percentage]{task.percentage:>3.0f}%"
        ),
        console=console,
    ) as progress:

        task = progress.add_task(
            "[cyan]Processing G-Code...",
            total=len(content)
        )

        for line in content:

            original_line = line
            stripped_line = line.strip()

            if stripped_line.startswith('M'):

                if not (
                    stripped_line.startswith('M106')
                    or stripped_line.startswith('M107')
                ):

                    progress.advance(task)
                    continue

            if (
                "syringe_diameter" in stripped_line
                or "nozzle_diameter" in stripped_line
                or "extrusion_coefficient" in stripped_line
            ):

                progress.advance(task)
                continue

            if (
                'G92 E0' in stripped_line
                or f'G92 {EXTRUSION_AXIS}0' in stripped_line
            ):

                e1 = 0.0
                e1_orig = 0.0

            if (
                not stripped_line
                or stripped_line.startswith(';')
                or 'G90' in stripped_line
                or 'G91' in stripped_line
                or 'G92' in stripped_line
                or 'G21' in stripped_line
                or 'G4' in stripped_line
            ):

                if (
                    ('G90' in stripped_line
                    or 'G91' in stripped_line)
                    and "G9" in original_line[:3]
                ):

                    progress.advance(task)
                    continue

                if 'G92' in stripped_line and 'E' in stripped_line:
                    f_new.write(
                        original_line.replace(
                            'E',
                            EXTRUSION_AXIS
                        )
                    )
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

            letters = {
                'G': None,
                'X': None,
                'Y': None,
                'Z': None,
                'A': None,
                'I': None,
                'J': None,
                'R': None,
                'T': None,
                'E': None,
                'F': None
            }

            for command in stripped_line.split():

                if command.startswith(';'):
                    break

                if command[0].upper() in letters:

                    try:
                        letters[command[0].upper()] = float(
                            command[1:]
                        )

                    except ValueError:
                        pass

            motion_axes = [
                'X',
                'Y',
                'Z',
                'A',
                'I',
                'J',
                'R',
                'T'
            ]

            if not any(
                letters.get(c) is not None
                for c in motion_axes
            ):

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

            x_val = (
                x if x is not None
                else (x1 if coordinate_type == 0 else 0)
            )

            y_val = (
                y if y is not None
                else (y1 if coordinate_type == 0 else 0)
            )

            z_val = (
                z if z is not None
                else (z1 if coordinate_type == 0 else 0)
            )

            a_val = (
                a if a is not None
                else (a1 if coordinate_type == 0 else 0)
            )

            i_val = i if i is not None else 0
            j_val = j if j is not None else 0

            if coordinate_type == 0:

                x_rel = (
                    (x_val - x1)
                    if x is not None else 0
                )

                y_rel = (
                    (y_val - y1)
                    if y is not None else 0
                )

                z_rel = (
                    (z_val - z1)
                    if z is not None else 0
                )

                a_rel = (
                    (a_val - a1)
                    if a is not None else 0
                )

            else:

                x_rel = x if x is not None else 0
                y_rel = y if y is not None else 0
                z_rel = z if z is not None else 0
                a_rel = a if a is not None else 0

            if g == 1:

                l = math.sqrt(
                    x_rel**2
                    + y_rel**2
                    + a_rel**2
                    + z_rel**2
                )

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

                            chunk = (
                                extrusion_coefficient
                                * l
                                * Z_NOZZLE_DIAMETER**2
                            ) / (
                                Z_SYRINGE_DIAMETER**2
                            )

                        else:

                            chunk = (
                                extrusion_coefficient
                                * l
                                * A_NOZZLE_DIAMETER**2
                            ) / (
                                A_SYRINGE_DIAMETER**2
                            )

                        if e_change < 0:
                            chunk = -chunk

                    else:

                        FILAMENT_DIAMETER = 1.75

                        if extruder == 0:

                            chunk = (
                                e_change
                                * (FILAMENT_DIAMETER**2)
                            ) / (
                                Z_SYRINGE_DIAMETER**2
                            )

                        else:

                            chunk = (
                                e_change
                                * (FILAMENT_DIAMETER**2)
                            ) / (
                                A_SYRINGE_DIAMETER**2
                            )

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

            if g is not None:
                write_line += 'G' + str(int(g))

            if x is not None:
                write_line += ' X' + str(x)

            if y is not None:
                write_line += ' Y' + str(y)

            if z is not None:
                write_line += ' Z' + str(z)

            if a is not None:
                write_line += ' A' + str(a)

            if e is not None and g != 0:

                write_line += (
                    f' {EXTRUSION_AXIS}'
                    + str(round(e, 3))
                )

            if f is not None:
                write_line += ' F' + str(f)

            f_new.write(write_line + "\n")

            if coordinate_type == 0:

                x1 = x_val if x is not None else x1
                y1 = y_val if y is not None else y1
                z1 = z_val if z is not None else z1
                a1 = a_val if a is not None else a1

            else:

                if x is not None:
                    x1 += x

                if y is not None:
                    y1 += y

                if z is not None:
                    z1 += z

                if a is not None:
                    a1 += a

            e1 = e if e is not None else e1

            progress.advance(task)

    if DO_AUTO_PRESSURIZE:

        f_new.write("\n; Auto-depressurize syringe\n")
        f_new.write("G91\n")

        f_new.write(
            f"G1 {EXTRUSION_AXIS}"
            f"{-PRESSURIZE_AMOUNT} "
            f"F{PRESSURIZE_SPEED}\n"
        )

        if COORDINATE_MODE == "G90":
            f_new.write("G90\n")

    f_new.write("\n; --- End Sequence ---\n")
    f_new.write("G91\n")
    f_new.write("G1 Z30 F300\n")
    f_new.write("G90\n")
    f_new.write("G1 X0 Y0 F300\n")

    f_new.close()

    netVol_Z = (
        netExtrude
        * math.pi
        * (Z_SYRINGE_DIAMETER / 2)**2
        / 1000
    )

    netVol_A = (
        netExtrude_A
        * math.pi
        * (A_SYRINGE_DIAMETER / 2)**2
        / 1000
    )

    success_text = (
        f"[bold cyan]Extruder B:[/bold cyan]\n"
        f"Distance: {round(netExtrude, 3)} mm\n"
        f"Volume: {round(netVol_Z, 3)} mL\n\n"
        f"[bold cyan]Extruder C:[/bold cyan]\n"
        f"Distance: {round(netExtrude_A, 3)} mm\n"
        f"Volume: {round(netVol_A, 3)} mL"
    )

    console.print()
    console.print(
        Panel(
            success_text,
            title="[bold green]Translation Complete[/bold green]",
            border_style="green"
        )
    )

    load_now = Prompt.ask(
        "\nLoad this file?",
        choices=["y", "n"],
        default="y"
    )

    if load_now.lower() == 'y':

        global loaded_filepath

        loaded_filepath = output_filepath

        console.print(
            f"[bold green]Loaded "
            f"{output_filename}![/bold green]"
        )

        time.sleep(1)


# ============================================================
# --- LOAD FILE MENU ---
# ============================================================

def load_file_menu():

    global loaded_filepath

    out_dir = "translated_gcode"

    if not os.path.exists(out_dir):

        console.print(
            Panel(
                f"[bold red]No '{out_dir}' directory.[/bold red]"
            )
        )

        time.sleep(2)
        return

    valid_extensions = ('.gcode', '.txt')

    files = [
        f for f in os.listdir(out_dir)
        if f.lower().endswith(valid_extensions)
    ]

    if not files:

        console.print(
            Panel(
                "[bold red]No translated files found.[/bold red]"
            )
        )

        time.sleep(2)
        return

    files.sort(
        key=lambda x:
        os.path.getmtime(os.path.join(out_dir, x)),
        reverse=True
    )

    file_table = Table(
        show_header=True,
        header_style="bold green",
        title="[bold cyan]Translated Files[/bold cyan]"
    )

    file_table.add_column("#")
    file_table.add_column("Filename")
    file_table.add_column("Modified")

    for i, f in enumerate(files):

        mtime = os.path.getmtime(
            os.path.join(out_dir, f)
        )

        dt_str = datetime.fromtimestamp(
            mtime
        ).strftime('%Y-%m-%d %H:%M:%S')

        file_table.add_row(
            str(i + 1),
            f,
            dt_str
        )

    console.print(file_table)

    console.print("[0] Cancel")

    choice = IntPrompt.ask(
        "[bold yellow]Select a file[/bold yellow]",
        choices=[str(i) for i in range(len(files) + 1)]
    )

    if choice == 0:
        return

    selected_file = files[choice - 1]

    loaded_filepath = os.path.join(
        out_dir,
        selected_file
    )

    console.print(
        f"\n[bold green]Loaded:[/bold green] "
        f"[cyan]{selected_file}[/cyan]"
    )

    time.sleep(1.5)


# ============================================================
# --- PRINT FILE ---
# ============================================================

def print_file():

    global printer_conn
    global loaded_filepath

    if not printer_conn:

        console.print(
            "[bold red]Printer not connected![/bold red]"
        )

        time.sleep(1)
        return

    if not loaded_filepath:

        console.print(
            "[bold red]No file loaded![/bold red]"
        )

        time.sleep(1)
        return

    try:

        with open(loaded_filepath, "r") as file:
            lines = file.readlines()

    except Exception as e:

        console.print(
            f"[bold red]Error reading file:[/bold red] {e}"
        )

        return

    console.print(
        Panel(
            f"[bold yellow]Starting print:[/bold yellow]\n"
            f"{os.path.basename(loaded_filepath)}",
            border_style="green"
        )
    )

    try:

        while not printer_response_queue.empty():
            printer_response_queue.get_nowait()

        send_gcode("M400")
        send_gcode("M114")

        with Progress(
            SpinnerColumn(),
            TextColumn(
                "[progress.description]{task.description}"
            ),
            BarColumn(bar_width=40),
            TextColumn(
                "[progress.percentage]{task.percentage:>3.0f}%"
            ),
            console=console,
        ) as progress:

            task = progress.add_task(
                "[cyan]Printing...",
                total=len(lines)
            )

            for raw_line in lines:

                stripped = raw_line.strip()

                if not stripped:

                    progress.advance(task)
                    continue

                if stripped.startswith(";"):

                    progress.advance(task)
                    continue

                command = stripped.split(";")[0].strip()

                if not command:

                    progress.advance(task)
                    continue

                send_gcode(command)

                progress.advance(task)

        console.print(
            "\n[bold green]"
            "Print completed successfully!"
            "[/bold green]"
        )

    except KeyboardInterrupt:

        console.print(
            "\n[bold red]Print interrupted.[/bold red]"
        )

        try:

            send_gcode("M400", wait_for_ok=False)
            send_gcode("G91", wait_for_ok=False)
            send_gcode("G1 Z20 F300", wait_for_ok=False)
            send_gcode("G90", wait_for_ok=False)

        except Exception:
            pass

    except Exception as e:

        console.print(
            f"\n[bold red]PRINT FAILED:[/bold red] {e}"
        )

        try:
            send_gcode("M400", wait_for_ok=False)
        except Exception:
            pass

    time.sleep(2)


# ============================================================
# --- GITHUB UPDATE ---
# ============================================================

def update_orca():

    global printer_conn

    console.print(
        Panel(
            "[bold cyan]Fetching updates...[/bold cyan]",
            border_style="cyan"
        )
    )

    try:

        result = subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True,
            check=True
        )

        console.print(
            "[bold green]Update complete![/bold green]"
        )

        if "Already up to date." in result.stdout:
            time.sleep(2)
            return

        console.print(
            "\n[bold yellow]Restarting...[/bold yellow]"
        )

        time.sleep(2)

        if printer_conn:

            try:
                printer_conn.close()
            except Exception:
                pass

            printer_conn = None

        os.execl(
            sys.executable,
            sys.executable,
            *sys.argv
        )

    except subprocess.CalledProcessError as e:

        console.print(
            "[bold red]Git update failed.[/bold red]"
        )

        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")

        time.sleep(3)

    except Exception as e:

        console.print(
            f"[bold red]Unexpected error:[/bold red] {e}"
        )

        time.sleep(3)


# ============================================================
# --- MAIN MENU ---
# ============================================================

def main():

    global printer_listener_running

    while True:

        console.clear()
        display_header()

        conn_status = (
            f"[bold green]"
            f"Connected ({printer_conn.port})"
            f"[/bold green]"
            if printer_conn
            else "[bold red]Not Connected[/bold red]"
        )

        console.print(f"Printer Status: {conn_status}")

        file_status = (
            f"[bold cyan]"
            f"{os.path.basename(loaded_filepath)}"
            f"[/bold cyan]"
            if loaded_filepath
            else "[dim]None[/dim]"
        )

        console.print(
            f"Loaded File:    {file_status}\n"
        )

        console.print(
            "[bold yellow]--- Main Menu ---[/bold yellow]"
        )

        valid_choices = ["1", "2", "3", "6", "7"]

        if printer_conn:

            console.print(
                "[0] [bold red]"
                "Reset Printer"
                "[/bold red]"
            )

            valid_choices.append("0")

        console.print("[1] Connect to Printer")
        console.print("[2] Translate G-Code")
        console.print("[3] Load Translated File")

        if printer_conn and loaded_filepath:

            console.print(
                "[4] [bold green]"
                "Print Loaded File"
                "[/bold green]"
            )

            valid_choices.append("4")

        else:

            console.print(
                "[4] [dim]"
                "Print Loaded File"
                "[/dim]"
            )

        if printer_conn:

            console.print(
                "[5] [bold cyan]"
                "Manual G-Code Terminal"
                "[/bold cyan]"
            )

            valid_choices.append("5")

        else:

            console.print(
                "[5] [dim]"
                "Manual G-Code Terminal"
                "[/dim]"
            )

        console.print("[6] Update ORCA")
        console.print("[7] Exit\n")

        valid_choices = sorted(set(valid_choices))

        choice = Prompt.ask(
            "[bold yellow]Choose an option[/bold yellow]",
            choices=valid_choices
        )

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
            update_orca()

        elif choice == "7":

            printer_listener_running = False

            if printer_conn:

                try:
                    printer_conn.close()

                except Exception:
                    pass

            console.print(
                "[bold magenta]Goodbye![/bold magenta]"
            )

            break


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        printer_listener_running = False

        if printer_conn:

            try:
                printer_conn.close()

            except Exception:
                pass

        console.print(
            "\n[bold magenta]Goodbye![/bold magenta]"
        )