@echo off
REM pg_up — 启动项目便携 PG 实例（PG17.5+pgvector，端口 5433，数据目录 data/pgdata）
REM 二进制复用 GEOFlow runtime；端口/listen_addresses 已写入 data/pgdata/postgresql.conf
setlocal
set PGCTL=E:\Downland\GEOFlow-v2.3.0\runtime\pgsql\bin\pg_ctl.exe
set PGDATA=%~dp0..\data\pgdata

REM 已监听则直接成功（幂等）
netstat -ano | findstr /R ":5433 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [pg_up] 5433 already listening.
  exit /b 0
)

echo [pg_up] starting postgres (recovery may take a while on first start)...
"%PGCTL%" -D "%PGDATA%" -l "%PGDATA%\pg.log" -t 120 start
if %errorlevel% neq 0 (
  REM pg_ctl 超时不代表失败——WAL 恢复可能超过等待窗，再探一次端口
  timeout /t 5 /nobreak >nul
)
netstat -ano | findstr /R ":5433 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [pg_up] 5433 is up.
  exit /b 0
)
echo [pg_up] FAILED - see %PGDATA%\pg.log
exit /b 1
