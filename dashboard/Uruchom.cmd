@echo off
cd /d "%~dp0"
echo Otworz http://127.0.0.1:4200/ w przegladarce.
node serve.mjs
pause
