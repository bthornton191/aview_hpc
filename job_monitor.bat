@echo off
call env\Scripts\activate.bat
start pythonw -m job_monitor
start http://localhost:8080
