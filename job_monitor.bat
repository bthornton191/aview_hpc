@echo off
call env\Scripts\activate.bat
wmic process where "commandline like '%pythonw.exe -m job_monitor'" call terminate
start pythonw -m job_monitor
start http://localhost:8080
