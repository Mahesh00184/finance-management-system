@echo off
title Finance Management Customer Portal

set FINANCE_SECRET_KEY=replace-this-with-a-long-random-secret

echo Starting customer website...
echo Website: http://127.0.0.1:8080
echo.

waitress-serve --listen=127.0.0.1:8080 web_app:app

pause