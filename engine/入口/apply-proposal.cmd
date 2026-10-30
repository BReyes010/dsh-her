@echo off
rem Apply a pending proposal into 人格\ (self-elevating). ASCII only.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','%~dp0apply-proposal.ps1'"
endlocal
