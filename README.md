# context-engineering-test

Тестовый проект для эксперимента с AI code review (context engineering).

## Структура

```
src/
  task_manager.py    — Python: веб-сервис управления задачами (auth, SQL, файлы)
cpp_module/
  data_processor.cpp — C++: парсинг CSV, агрегация, статистика, многопоточность
  Makefile
tests/
  test_task_manager.py — pytest-тесты
```

## Запуск

```bash
# Python
pip install -r requirements.txt
python src/task_manager.py
pytest tests/

# C++
cd cpp_module && make && ./data_processor input.csv
```

## Эксперимент

Проект используется для тестирования CodeRabbit — AI-системы code review.
Цель: проверить, как context engineering влияет на качество поиска багов.
