@echo off
setlocal
set "CHARACTER_CANVAS_DELIVERY=%~dp0"
set "CHARACTER_CANVAS_PROJECT=%~1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& (Join-Path $env:CHARACTER_CANVAS_DELIVERY ([string][char]0x7F51 + [char]0x9875 + [char]0x8D44 + [char]0x6E90 + '\launch.ps1')) -Project $env:CHARACTER_CANVAS_PROJECT"
if errorlevel 1 pause
