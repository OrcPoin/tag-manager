@echo off
rem Запуск Tag Manager. Двойной клик — и приложение откроется в браузере.
rem Через "python -m streamlit", чтобы не зависеть от streamlit.exe в PATH.
cd /d "%~dp0"
python -m streamlit run "%~dp0app.py"
pause
