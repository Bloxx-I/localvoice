@echo off
rem LocalVoice installieren: Doppelklick genuegt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install-windows.ps1" %*
if errorlevel 1 (echo. & echo Installation fehlgeschlagen. Details siehe oben.) else (echo. & echo Fertig. LocalVoice liegt jetzt auf dem Desktop und im Startmenue.)
pause
