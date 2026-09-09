import os
import sys
import shutil
import subprocess


# ============================================================
# PROJECT DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

os.chdir(BASE_DIR)


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "FinanceManagement"

MAIN_FILE = "main.py"

DIST_DIR = os.path.join(
    BASE_DIR,
    "dist"
)

BUILD_DIR = os.path.join(
    BASE_DIR,
    "build"
)


# ============================================================
# HELPER
# ============================================================

def remove_directory(path):
    if os.path.exists(path):
        print(f"Removing: {path}")

        try:
            shutil.rmtree(path)
        except Exception as error:
            print(f"Could not remove {path}: {error}")
            sys.exit(1)


def remove_file(path):
    if os.path.exists(path):
        print(f"Removing: {path}")

        try:
            os.remove(path)
        except Exception as error:
            print(f"Could not remove {path}: {error}")
            sys.exit(1)


# ============================================================
# CHECK FILES
# ============================================================

print()
print("=" * 65)
print("             FINANCE MANAGEMENT EXE BUILDER")
print("=" * 65)
print()

if not os.path.isfile(MAIN_FILE):
    print("ERROR: main.py was not found.")
    sys.exit(1)


if not os.path.isfile("logo.png"):
    print("WARNING: logo.png was not found.")

if not os.path.isdir("templates"):
    print("WARNING: templates folder was not found.")


# ============================================================
# CHECK PYINSTALLER
# ============================================================

print()
print("Checking PyInstaller...")

check_pyinstaller = subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--version"
    ]
)

if check_pyinstaller.returncode != 0:
    print()
    print("ERROR: PyInstaller is not installed.")
    print()
    print("Run:")
    print("python -m pip install pyinstaller")
    sys.exit(1)


# ============================================================
# CLEAN OLD BUILD
# ============================================================

print()
print("Cleaning old build files...")

remove_directory(BUILD_DIR)
remove_directory(DIST_DIR)

remove_file(
    os.path.join(
        BASE_DIR,
        f"{APP_NAME}.spec"
    )
)


# ============================================================
# PYINSTALLER COMMAND
# ============================================================

cmd = [
    sys.executable,
    "-m",
    "PyInstaller",

    # Clean
    "--clean",

    # Confirm automatically
    "--noconfirm",

    # Single EXE
    "--onefile",

    # No console window
    "--windowed",

    # Application name
    "--name",
    APP_NAME,

    # ========================================================
    # IMPORTANT
    # ttkbootstrap resources
    # ========================================================

    "--collect-all",
    "ttkbootstrap",

    # ========================================================
    # Pillow
    # ========================================================

    "--collect-all",
    "PIL",

    # ========================================================
    # ReportLab
    # ========================================================

    "--collect-all",
    "reportlab",

    # ========================================================
    # Waitress
    # ========================================================

    "--collect-all",
    "waitress",

    # ========================================================
    # Application resources
    # ========================================================

    "--add-data",
    "logo.png;.",

    "--add-data",
    "templates;templates",

    # ========================================================
    # Unused large modules
    # ========================================================

    "--exclude-module",
    "torch",

    "--exclude-module",
    "scipy",

    "--exclude-module",
    "matplotlib",

    "--exclude-module",
    "pygame",

    "--exclude-module",
    "cv2",

    "--exclude-module",
    "tensorboard",

    "--exclude-module",
    "notebook",

    "--exclude-module",
    "IPython",

    "--exclude-module",
    "jupyter",

    "--exclude-module",
    "numba",

    "--exclude-module",
    "sklearn",

    "--exclude-module",
    "tensorflow",

    "--exclude-module",
    "Keras",

    "--exclude-module",
    "bokeh",

    "--exclude-module",
    "plotly",

    "--exclude-module",
    "dask",

    "--exclude-module",
    "sympy",

    "--exclude-module",
    "statsmodels",

    "--exclude-module",
    "seaborn",

    # ========================================================
    # MAIN APPLICATION
    # ========================================================

    MAIN_FILE,
]


# ============================================================
# SHOW COMMAND
# ============================================================

print()
print("=" * 65)
print("BUILD COMMAND")
print("=" * 65)
print()

print(" ".join(cmd))

print()
print("=" * 65)
print("BUILDING...")
print("=" * 65)
print()


# ============================================================
# RUN PYINSTALLER
# ============================================================

result = subprocess.run(
    cmd,
    cwd=BASE_DIR
)


# ============================================================
# RESULT
# ============================================================

if result.returncode != 0:

    print()
    print("=" * 65)
    print("                    BUILD FAILED")
    print("=" * 65)
    print()

    print("PyInstaller returned error code:")
    print(result.returncode)

    print()

    sys.exit(result.returncode)


# ============================================================
# EXE PATH
# ============================================================

exe_path = os.path.join(
    DIST_DIR,
    f"{APP_NAME}.exe"
)


# ============================================================
# VERIFY EXE
# ============================================================

if not os.path.isfile(exe_path):

    print()
    print("=" * 65)
    print("ERROR: EXE WAS NOT CREATED")
    print("=" * 65)
    print()

    sys.exit(1)


# ============================================================
# SUCCESS
# ============================================================

file_size = os.path.getsize(
    exe_path
)


print()
print("=" * 65)
print("                 BUILD SUCCESSFUL")
print("=" * 65)
print()

print("Application:")
print(APP_NAME)

print()

print("EXE:")
print(exe_path)

print()

print("Size:")
print(f"{file_size:,} bytes")

print()

print("=" * 65)
print("You can now run the EXE.")
print("=" * 65)
print()