@echo off
chcp 65001 >nul
title 人物设定图后端服务
setlocal
set "CHARACTER_CANVAS_RESOURCE=%~dp0"
set "CHARACTER_CANVAS_PROJECT=%~1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& (Join-Path $env:CHARACTER_CANVAS_RESOURCE 'launch.ps1') -Project $env:CHARACTER_CANVAS_PROJECT"
if errorlevel 1 pause
