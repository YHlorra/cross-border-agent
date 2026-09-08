@echo off
REM Agent server launcher — start /B spawns a fully detached child process
REM (new process group, no shared stdin with the parent shell). The parent
REM returns immediately; the child survives the bash tool's task lifecycle.
REM
REM Usage: scripts\start_agent_server.cmd
REM Log:   .venv\server.log
REM Stop:  taskkill /IM python.exe /FI "PID eq <pid>"  (or netstat :8765)

setlocal
cd /d "E:\Desktop\Cross-border"

start /B "" ".venv\Scripts\python.exe" -m agent.server > ".venv\server.log" 2>&1
endlocal
