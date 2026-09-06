@echo off
cd /d "%~dp0"
claude --safe-mode --append-system-prompt-file "%~dp0persona.md" %*
