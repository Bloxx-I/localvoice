@echo off
rem LocalVoice entfernen. Aufnahmen in Dokumente\LocalVoice Aufnahmen bleiben erhalten.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\uninstall-windows.ps1" %*
pause
