@echo off
REM AdminBoeker Windows launcher.
REM Dubbelklik dit bestand om de server te starten.

echo.
echo =========================================
echo  AdminBoeker - starten
echo =========================================
echo.

REM Eerste keer op DEZE computer? Installeer dependencies.
REM Markering is per-computer (%COMPUTERNAME%) zodat een gesyncte map
REM (Google Drive/Dropbox) op elke computer z'n eigen install doet.
set "DEPSFLAG=.deps_%COMPUTERNAME%"
if not exist "%DEPSFLAG%" (
    echo Eerste keer op deze computer - dependencies installeren...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo FOUT: pip install mislukt. Heb je Python geinstalleerd?
        pause
        exit /b 1
    )
    type nul > "%DEPSFLAG%"
    echo.
)

REM Start de server
python app.py

echo.
echo Server gestopt.
pause
