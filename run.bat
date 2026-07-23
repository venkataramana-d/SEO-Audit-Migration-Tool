@echo off
REM One-click launcher for the SEO Pre-Migration Audit Tool
cd /d "%~dp0"
echo Starting SEO Audit Tool...
echo Open http://127.0.0.1:5000 in your browser
echo Press Ctrl+C in this window to stop.
python app.py
pause
