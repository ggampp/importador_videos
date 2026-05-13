@echo off
REM Launcher para o importador LingQ. Use para execucao manual.
cd /d "%~dp0"
echo Executando importador LingQ...
python import_videos.py
echo.
echo Finalizado. Pressione qualquer tecla para fechar.
pause >nul
