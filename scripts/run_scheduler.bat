@echo off
title AutoYT Podcast Shorts 24x7 Scheduler
echo ========================================================
echo   AutoYT Automated Podcast Discovery & 5-Shorts Scheduler
echo   Runs: 08:00 (discover), 14:00 (process), 18:00 (queue prep)
echo   Publishing Window: 19:00, 19:30, 20:00, 20:30, 21:00 IST
echo ========================================================
cd /d "%~dp0\.."
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m autoyt podcast scheduler
) else (
    python -m autoyt podcast scheduler
)
pause
