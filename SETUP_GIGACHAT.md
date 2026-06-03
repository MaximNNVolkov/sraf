# Подключение GigaChat к проекту

## 1. Получить Authorization key

1. Перейти на https://developers.sber.ru
2. Зарегистрироваться или войти
3. Перейти в личный кабинет
4. Получить **Authorization key** для GigaChat API

## 2. Установить Authorization key в .env

Отредактируйте `/home/user1/lab_agent/.env`:

```bash
# Get your authorization key from https://developers.sber.ru
GIGACHAT_CREDENTIALS=your-authorization-key-here
```

Ключ выглядит примерно так:
```
MGU5NjI4YmItNGY1YS00YWFjLThhYjAtZTJhMjU0YmRjMDQ0OjIyZTA2OTg1LWUwYTctNDcwZC1iODc1LTliOGNlYmE0MGJmNg==
```

## 3. Загрузить переменные окружения

```bash
cd /home/user1/lab_agent
source .venv/bin/activate
export $(cat .env | grep -v '#' | xargs)
```

Или используйте скрипт:
```bash
./run_sraf.sh
```

## 4. Протестировать подключение

```bash
sraf run "Привет, напиши короткое письмо" --demo
```

После получения Authorization key:
```bash
sraf run "Привет, напиши короткое письмо"
```

## Как работает авторизация

Процесс полностью автоматизирован в библиотеке `gigachat`:

```
1. Authorization key (из GIGACHAT_CREDENTIALS)
   ↓
2. POST /api/v2/oauth → получить Access token
   ↓
3. Access token (действует 30 минут)
   ↓
4. Использование в запросах: Authorization: Bearer <access_token>
   ↓
5. Автоматическое обновление token когда истекает
```

## Переменные окружения

| Переменная | Значение | Обязательна |
|---|---|---|
| `GIGACHAT_CREDENTIALS` | Authorization key | ✅ Да |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` (default) или `GIGACHAT_API_B2B`, `GIGACHAT_API_CORP` | ❌ Нет |
| `GIGACHAT_MODEL` | Название модели (default: `GigaChat`) | ❌ Нет |
| `GIGACHAT_VERIFY_SSL_CERTS` | `true`/`false` (default: `true`) | ❌ Нет |

## Режим Demo (без credentials)

Для тестирования без Authorization key:

```bash
sraf run "test query" --demo
sraf chat --demo
```

## Устранение проблем

### Error: "GIGACHAT_CREDENTIALS is required"
→ Переменная окружения не установлена. Проверьте `.env` файл и выполните `export`

### Error: "SSL certificate verification failed"
→ Добавьте флаг при запуске:
```bash
sraf run "test" --no-verify-ssl
sraf chat --no-verify-ssl
```

### Access token истек
→ Библиотека автоматически получает новый. Перезагрузите переменные окружения и попробуйте снова.
