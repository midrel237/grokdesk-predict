@echo off
REM Retire la tache planifiee du recorder et arrete le processus en cours.

setlocal
set TASK=GrokDesk Recorder

schtasks /end /tn "%TASK%" 2>nul
schtasks /delete /tn "%TASK%" /f
if errorlevel 1 (
  echo La tache "%TASK%" n'existait pas.
) else (
  echo Tache "%TASK%" supprimee.
)
echo.
echo Les donnees deja archivees dans data\ ne sont pas touchees.
