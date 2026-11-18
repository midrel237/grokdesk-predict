@echo off
REM Lanceur du recorder GROK DESK, avec relance automatique.
REM
REM Enregistre le carnet et les payouts Polymarket Up/Down en continu. La
REM boucle relance le processus s'il meurt (coupure reseau, erreur API), avec
REM 30 s d'attente pour ne pas marteler l'API en cas de panne durable.
REM
REM Installation en tache planifiee : scripts\install-task.cmd
REM Desinstallation                 : scripts\uninstall-task.cmd

setlocal
cd /d "%~dp0.."

set PYTHON=python
set ASSETS=btc,eth,sol
set POLL=5
set DB=data\parquet
set LOGDIR=logs

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "data" mkdir "data"

:loop
echo [%DATE% %TIME%] demarrage du recorder >> "%LOGDIR%\recorder-supervisor.log"
"%PYTHON%" -m grokdesk.record --assets %ASSETS% --poll %POLL% --db "%DB%" --format parquet --quiet >> "%LOGDIR%\recorder.log" 2>&1
echo [%DATE% %TIME%] arret (code %ERRORLEVEL%), relance dans 30 s >> "%LOGDIR%\recorder-supervisor.log"
timeout /t 30 /nobreak > nul
goto loop
