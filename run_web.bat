@echo off
REM Launcher para a interface web local do importador LingQ.
cd /d "%~dp0"
echo Abrindo servidor web em http://127.0.0.1:5000
python -m uvicorn app:app --host 127.0.0.1 --port 5000 --no-access-log
