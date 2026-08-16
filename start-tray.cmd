@echo off
rem DSH Web Tray launcher - starts the tray in the background with NO console window.
rem pythonw.exe is the GUI-subsystem interpreter: it never attaches to a console,
rem so closing any terminal (or this brief flash window) never kills the tray.
setlocal
set "ROOT=%~dp0"

rem Resolve pythonw.exe: PATH first, then common install locations (conda/python.org)
set "PYW="
for /f "usebackq delims=" %%i in (`where pythonw.exe 2^>nul`) do (
    if not defined PYW set "PYW=%%i"
)
if not defined PYW if exist "%USERPROFILE%\anaconda3\pythonw.exe" set "PYW=%USERPROFILE%\anaconda3\pythonw.exe"
if not defined PYW if exist "C:\ProgramData\anaconda3\pythonw.exe" set "PYW=C:\ProgramData\anaconda3\pythonw.exe"
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python311\pythonw.exe"

if not defined PYW (
    echo [dsh-web-tray] pythonw.exe not found. Add Python to PATH or edit this file's PYW line.
    pause
    exit /b 1
)

start "" "%PYW%" "%ROOT%dsh-web-tray.py"
endlocal
