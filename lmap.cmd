@echo off
rem lmap.cmd - launches Layout Mapper UI (no console window if pythonw/pyw exists)
rem Usage: lmap.cmd            -> open UI where you left off
rem        lmap.cmd "D:\path"  -> open UI with that folder as root
set "HERE=%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "%HERE%layout_maper_ui.py" %* & exit /b)
where pyw >nul 2>nul && (start "" pyw "%HERE%layout_maper_ui.py" %* & exit /b)
python "%HERE%layout_maper_ui.py" %*
