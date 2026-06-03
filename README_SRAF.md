# SRAF с GigaChat API

Полностью рабочая интеграция SRAF Framework с GigaChat API.

## Быстрый старт

### 1. Установка зависимостей

```bash
# Активируем виртуальное окружение
source .venv/bin/activate

# Зависимости уже установлены (можно проверить)
pip install -e .
```

### 2. Настройка учетных данных

```bash
# Файл .env уже содержит GIGACHAT_CREDENTIALS
# Убедитесь, что переменная установлена:
cat .env | grep GIGACHAT_CREDENTIALS
```

### 3. Базовое использование

#### Demo режим (без интернета)
```bash
# Не требует интернета или учетных данных
sraf run "Напиши приветствие" --demo

# Интерактивный чат
sraf chat --demo
```

#### С реальным API
```bash
# Основная задача
sraf run "Напиши короткое приветствие на русском языке" --no-verify-ssl

# Интерактивный чат
echo "Напиши стихотворение" | sraf chat --no-verify-ssl
```

## Решение проблемы SSL

Если вы получаете ошибку SSL, используйте флаг `--no-verify-ssl`:

```bash
# Правильно (работает):
sraf run "task" --no-verify-ssl

# Неправильно (ошибка):
sraf run "task"  # SSL: CERTIFICATE_VERIFY_FAILED
```

Флаг применяется для локальных окружений с корпоративным прокси и самоподписанными сертификатами.

## Параметры команды

### sraf run
```bash
sraf run "ЗАДАЧА" [ОПЦИИ]

Опции:
  --demo                   Использовать demo режим (без API)
  --no-verify-ssl          Отключить проверку SSL сертификатов
  --max-steps N            Максимум шагов (по умолчанию 5)
  --max-attempts N         Максимум попыток (по умолчанию 3)
  --base-prompt-file FILE  Файл с базовым промптом
```

### sraf chat
```bash
sraf chat [ОПЦИИ]

Опции:
  --demo                   Использовать demo режим (без API)
  --no-verify-ssl          Отключить проверку SSL сертификатов
  --max-history-turns N    Размер истории в памяти
```

## Среда окружения

### GIGACHAT_CREDENTIALS (обязательно)
Base64-кодированная пара `client_id:client_secret`:
```bash
export GIGACHAT_CREDENTIALS="base64(client_id:client_secret)"
```

### GIGACHAT_SCOPE (опционально)
```bash
# По умолчанию: GIGACHAT_API_PERS
export GIGACHAT_SCOPE=GIGACHAT_API_PERS     # Личный API
export GIGACHAT_SCOPE=GIGACHAT_API_B2B      # B2B API
export GIGACHAT_SCOPE=GIGACHAT_API_CORP     # Корпоративный API
```

### GIGACHAT_MODEL (опционально)
```bash
# По умолчанию: GigaChat (последняя стабильная версия)
export GIGACHAT_MODEL=GigaChat
```

## Примеры

### Пример 1: Простая задача
```bash
sraf run "Напиши краткое резюме о Python" --no-verify-ssl --max-steps 1
```

### Пример 2: Интерактивный чат
```bash
sraf chat --no-verify-ssl
# > Напиши стихотворение о коде
# Результат появится ниже
# > :q  (для выхода)
```

### Пример 3: Demo режим
```bash
# Быстрый тест без интернета
sraf run "test" --demo
sraf chat --demo
```

## Архитектура

```
SRAF Loop:
1. Extractor    → Извлекает инструкции из задачи
2. Validator    → Проверяет соответствие инструкций
3. Executor     → Выполняет инструкции (с инструментами)
4. Evaluator    → Оценивает результат
5. Refiner      → Уточняет при необходимости
```

## Логирование и отладка

```bash
# С подробным выводом
sraf run "задача" --no-verify-ssl 2>&1 | less

# Только ошибки
sraf run "задача" --no-verify-ssl 2>/dev/null
```

## Тестирование

```bash
# Запуск тестов
python -m pytest tests/

# С покрытием
python -m pytest tests/ --cov=sraf
```

## Лучшие практики

1. **Всегда используйте `.env` файл** для хранения учетных данных
2. **Включайте `.env` в `.gitignore`** чтобы не коммитить секреты
3. **Используйте `--demo` для разработки** - быстрее и дешевле
4. **Используйте `--no-verify-ssl` только локально** - корпоративный прокси
5. **Ограничивайте `--max-steps`** для экономии запросов к API

## Решение проблем

### Ошибка: "GIGACHAT_CREDENTIALS is required"
```bash
# Решение: установите переменную окружения
export $(cat .env | grep -v '#' | xargs)
```

### Ошибка: "SSL: CERTIFICATE_VERIFY_FAILED"
```bash
# Решение: используйте флаг --no-verify-ssl
sraf run "задача" --no-verify-ssl
```

### Ошибка: "Connection refused"
```bash
# Проверьте интернет соединение
# Проверьте, что GigaChat API доступен
curl -I https://gigachat.devices.sberbank.ru/
```

## Дополнительные ресурсы

- [GigaChat API Documentation](https://developers.gigachat.devices.sberbank.ru/)
- [SRAF Documentation](https://github.com/geekan/MetaGPT)
- [HTTP Client (HTTPX) Documentation](https://www.python-httpx.org/)
