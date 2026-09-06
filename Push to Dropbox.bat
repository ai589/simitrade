@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\folder creator - clean\Push to Dropbox.ps1" -Path "%~dp0output"
