@echo off
setlocal

title Finance Management - EXE Builder

echo.
echo ============================================================
echo              FINANCE MANAGEMENT EXE BUILDER
echo ============================================================
echo.

cd /d "%~dp0"

echo Project folder:
echo %CD%
echo.

echo ============================================================
echo [1/6] Checking required files
echo ============================================================
echo.

if not exist "main.py" (
    echo ERROR: main.py not found.
    pause
    exit /b 1
)

if not exist "logo.png" (
    echo WARNING: logo.png not found.
    echo The application may still build without the logo.
    echo.
)

if not exist "templates" (
    echo WARNING: templates folder not found.
    echo.
)

echo Required files check completed.
echo.

echo ============================================================
echo [2/6] Checking Python
echo ============================================================
echo.

python --version

if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b 1
)

echo.

echo ============================================================
echo [3/6] Checking PyInstaller
echo ============================================================
echo.

python -m PyInstaller --version

if errorlevel 1 (
    echo.
    echo PyInstaller is not installed.
    echo Installing PyInstaller...
    echo.

    python -m pip install pyinstaller

    if errorlevel 1 (
        echo.
        echo ERROR: Could not install PyInstaller.
        pause
        exit /b 1
    )
)

echo.

echo ============================================================
echo [4/6] Cleaning previous build
echo ============================================================
echo.

if exist "build" (
    echo Removing build folder...
    rmdir /s /q "build"
)

if exist "dist" (
    echo Removing dist folder...
    rmdir /s /q "dist"
)

if exist "FinanceManagement.spec" (
    echo Removing old generated spec...
    del /q "FinanceManagement.spec"
)

echo.
echo Old build files cleaned.
echo.

echo ============================================================
echo [5/6] Building FinanceManagement.exe
echo ============================================================
echo.

python -m PyInstaller ^
    --clean ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "FinanceManagement" ^
    --collect-all "ttkbootstrap" ^
    --collect-all "PIL" ^
    --collect-all "reportlab" ^
    --collect-all "waitress" ^
    --add-data "logo.png;." ^
    --add-data "templates;templates" ^
    --exclude-module "torch" ^
    --exclude-module "scipy" ^
    --exclude-module "matplotlib" ^
    --exclude-module "pygame" ^
    --exclude-module "cv2" ^
    --exclude-module "tensorboard" ^
    --exclude-module "notebook" ^
    --exclude-module "IPython" ^
    --exclude-module "jupyter" ^
    --exclude-module "numba" ^
    --exclude-module "sklearn" ^
    --exclude-module "tensorflow" ^
    --exclude-module "Keras" ^
    --exclude-module "bokeh" ^
    --exclude-module "plotly" ^
    --exclude-module "dask" ^
    --exclude-module "sympy" ^
    --exclude-module "statsmodels" ^
    --exclude-module "seaborn" ^
    "main.py"

if errorlevel 1 (
    echo.
    echo ============================================================
    echo                     BUILD FAILED
    echo ============================================================
    echo.
    echo Please read the PyInstaller error above.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [6/6] Checking EXE
echo ============================================================
echo.

if not exist "dist\FinanceManagement.exe" (
    echo.
    echo ERROR:
    echo FinanceManagement.exe was not created.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo                  BUILD SUCCESSFUL
echo ============================================================
echo.

echo EXE created at:
echo.
echo %CD%\dist\FinanceManagement.exe
echo.

echo File size:
for %%A in ("dist\FinanceManagement.exe") do echo %%~zA bytes

echo.
echo Opening dist folder...
start "" "%CD%\dist"

echo.
echo ============================================================
echo You can now run:
echo dist\FinanceManagement.exe
echo ============================================================
echo.

pause
endlocal