# Подключение GigaChat: Полное руководство

## 📋 Процесс авторизации GigaChat

### Шаг 1: Что такое Authorization key?

**Authorization key** — это ключ доступа, который вы получаете с портала разработчика Sber.
Он кодируется в формате Base64 и выглядит примерно так:

```
MGU5NjI4YmItNGY1YS00YWFjLThhYjAtZTJhMjU0YmRjMDQ0OjIyZTA2OTg1LWUwYTctNDcwZC1iODc1LTliOGNlYmE0MGJmNg==
```

### Шаг 2: Полный цикл авторизации (что происходит автоматически)

```
┌─────────────────────────────────────────────────────────────────┐
│                     Ваше приложение (SRAF)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (1) Инициализация
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    GigaChatClient (библиотека)                 │
│  • Читает GIGACHAT_CREDENTIALS из переменной окружения         │
│  • Хранит Authorization key                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (2) Первый запрос к API
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               POST /api/v2/oauth (получить token)              │
│  Authorization: Basic <Authorization_key>                       │
│  Payload: {'scope': 'GIGACHAT_API_PERS'}                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (3) Ответ от GigaChat
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Access Token (действует 30 минут)                 │
│  Пример: eyJhbGciOiJSUzI1NiIsInR5cC...                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (4) Используется для запросов
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Запросы к GigaChat API                      │
│  Authorization: Bearer <Access_Token>                           │
│  • GET /api/v1/models/                                          │
│  • POST /api/v1/chat/completions                               │
│  • ...                                                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (5) Token истекает (30 мин)
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│            Автоматическое обновление token                    │
│  • Библиотека повторно обращается к /api/v2/oauth             │
│  • Получает новый Access Token                                  │
│  • Продолжает работу без прерывания                            │
└─────────────────────────────────────────────────────────────────┘
```

## 🔧 Как настроить в проекте

### 1. Получить Authorization key

Перейдите на https://developers.sber.ru/:

1. Зарегистрируйтесь или войдите в аккаунт
2. Перейдите в личный кабинет
3. Найдите раздел GigaChat API
4. Получите свой **Authorization key**

### 2. Добавить в переменные окружения

Отредактируйте файл `.env` в корне проекта:

```bash
# .env file
GIGACHAT_CREDENTIALS=your-authorization-key-here
```

**Важно:** Никогда не коммитьте `.env` файл в git!

### 3. Загрузить переменные в текущую сессию

```bash
# Вариант 1: Явно
export GIGACHAT_CREDENTIALS="your-key"

# Вариант 2: Из файла .env
export $(cat .env | grep -v '#' | xargs)

# Вариант 3: Использовать скрипт
source .venv/bin/activate
./run_sraf.sh  # автоматически загружает .env
```

### 4. Проверить подключение

```bash
# Вариант 1: Используя встроенный пример
python examples/gigachat_auth_example.py

# Вариант 2: Используя SRAF
sraf run "Привет, напиши письмо" --max-steps 2
```

## 📝 Примеры использования

### Запуск в демо-режиме (без credentials)

```bash
sraf run "напиши функцию факториала" --demo
sraf chat --demo
```

### Запуск с реальными credentials

```bash
source .venv/bin/activate
export $(cat .env | xargs)  # загрузить GIGACHAT_CREDENTIALS

# Одиночный запрос
sraf run "напиши функцию быстрой сортировки"

# Интерактивный чат
sraf chat
```

### Запуск с дополнительными опциями

```bash
# Увеличить количество попыток и шагов
sraf run "задача" --max-attempts 5 --max-steps 10

# Использовать собственный prompt
sraf run "задача" --base-prompt-file custom_prompt.txt

# Отключить SSL проверку (только для локального прокси)
sraf run "задача" --no-verify-ssl
```

## 🐍 Пример: Получение token вручную

Если вы хотите работать с API напрямую без библиотеки `gigachat`:

```python
import requests
import uuid
import base64
import os

# Получить Authorization key
auth_key = os.getenv('GIGACHAT_CREDENTIALS')

# Шаг 1: Получить Access Token
url_token = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
headers_token = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Accept': 'application/json',
    'RqUID': str(uuid.uuid4()),
    'Authorization': f'Basic {auth_key}',
}
payload_token = {'scope': 'GIGACHAT_API_PERS'}

response = requests.post(url_token, headers=headers_token, data=payload_token, verify=False)
access_token = response.json()['access_token']

# Шаг 2: Использовать Access Token для запроса к API
url_api = "https://gigachat.devices.sberbank.ru/api/v1/models/"
headers_api = {
    'Accept': 'application/json',
    'Authorization': f'Bearer {access_token}',
}

response = requests.get(url_api, headers=headers_api, verify=False)
models = response.json()
print(models)
```

Запустите:
```bash
export $(cat .env | xargs)
python your_script.py
```

## 🆘 Устранение проблем

| Ошибка | Причина | Решение |
|--------|---------|--------|
| `GIGACHAT_CREDENTIALS is required` | Переменная окружения не установлена | Выполните `export GIGACHAT_CREDENTIALS="..."` |
| `SSL certificate verification failed` | Проблема с сертификатом | Добавьте флаг `--no-verify-ssl` |
| `401 Unauthorized` | Неверный Authorization key | Проверьте ключ на портале разработчика |
| `429 Too Many Requests` | Превышены лимиты API | Подождите некоторое время и повторите |

## 📚 Документация

- [GigaChat SDK (GitHub)](https://github.com/ai-forever/gigachat)
- [GigaChat API (Sber)](https://developers.sber.ru/docs/ru/gigachat/api/main)
- [Быстрый старт](https://developers.sber.ru/docs/ru/gigachat/quickstart/main)

## ⚙️ Переменные окружения

```bash
# Обязательна
GIGACHAT_CREDENTIALS=your-key

# Опциональны
GIGACHAT_SCOPE=GIGACHAT_API_PERS              # PERS|B2B|CORP
GIGACHAT_MODEL=GigaChat                       # Модель по умолчанию
GIGACHAT_VERIFY_SSL_CERTS=true                # true/false
GIGACHAT_CA_BUNDLE_FILE=/path/to/certs.pem  # Для собственных сертификатов
```

## 💡 Tips

1. **Token действует 30 минут** — библиотека автоматически получает новый при истечении
2. **Не коммитьте credentials** — добавьте `.env` в `.gitignore`
3. **Для разработки используйте `--demo`** — тестируйте без credentials
4. **Проверяйте лимиты API** — есть ограничения по количеству запросов
