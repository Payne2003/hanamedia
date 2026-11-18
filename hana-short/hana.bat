@echo off
rem HanaMedia Short CLI - usage: hana <command> ...   (see README.md)
setlocal
set PYTHONIOENCODING=utf-8
python "%~dp0hana_cli.py" %*
