@echo off
REM pg_down — 停止项目便携 PG 实例（data/pgdata，5433）
setlocal
set PGCTL=E:\Downland\GEOFlow-v2.3.0\runtime\pgsql\bin\pg_ctl.exe
set PGDATA=%~dp0..\data\pgdata

netstat -ano | findstr /R ":5433 .*LISTENING" >nul
if %errorlevel% neq 0 (
  echo [pg_down] 5433 not running.
  exit /b 0
)
"%PGCTL%" -D "%PGDATA%" -t 60 stop -m fast
if %errorlevel% equ 0 (
  echo [pg_down] stopped.
) else (
  echo [pg_down] stop failed - see %PGDATA%\pg.log
  exit /b 1
)
