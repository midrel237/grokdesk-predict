@echo off
REM Installe le recorder en tache planifiee Windows (au niveau utilisateur,
REM sans elevation). Il demarre a l'ouverture de session et survit donc aux
REM redemarrages.
REM
REM Pour le retirer : scripts\uninstall-task.cmd

setlocal
set TASK=GrokDesk Recorder
set CMD=%~dp0recorder.cmd

echo Installation de la tache planifiee "%TASK%"
echo   commande : %CMD%
echo.

schtasks /create /tn "%TASK%" /tr "\"%CMD%\"" /sc onlogon /rl limited /f
if errorlevel 1 (
  echo.
  echo ECHEC. Verifier que les taches planifiees ne sont pas restreintes
  echo par une politique de l'entreprise.
  exit /b 1
)

echo.
echo Installee. Commandes utiles :
echo   schtasks /run    /tn "%TASK%"     demarrer maintenant
echo   schtasks /end    /tn "%TASK%"     arreter
echo   schtasks /query  /tn "%TASK%" /v  etat detaille
echo.
echo Les journaux arrivent dans logs\recorder.log et
echo logs\recorder-supervisor.log
