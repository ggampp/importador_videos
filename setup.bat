@echo off
REM Setup inicial: instala dependencias Python e abre o Edge para configurar a extensao LingQ.
cd /d "%~dp0"

echo === Passo 1/3: Instalando dependencias Python ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias. Verifique se Python esta no PATH.
    pause
    exit /b 1
)

echo.
echo === Passo 2/3: Instalando browser Edge no Playwright ===
python -m playwright install msedge

echo.
echo === Passo 3/3: Abrindo Edge para voce instalar extensao LingQ e fazer login ===
python setup_profile.py

echo.
echo Setup concluido. Agora rode "run.bat" para testar uma importacao manual.
pause
