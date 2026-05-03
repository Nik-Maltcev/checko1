#!/bin/bash
# Запускаем Flask UI в фоне
python app.py &

# Запускаем скрипт регистрации
python register_checko.py

# Держим Flask живым после завершения регистрации
wait
