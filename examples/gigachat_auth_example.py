#!/usr/bin/env python3
"""
Пример: Как работает авторизация в GigaChat API

Этот скрипт показывает полный процесс:
1. Получить Access token используя Authorization key
2. Отправить запрос к API используя Access token
3. Получить список моделей
"""

import requests
import json
import os
import uuid
from typing import Optional


def get_access_token(auth_key: str) -> Optional[str]:
    """
    Шаг 1: Получить Access token используя Authorization key
    
    Это эквивалентно: POST /api/v2/oauth с Basic Authentication
    """
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': str(uuid.uuid4()),
        'Authorization': f'Basic {auth_key}',  # Authorization key кодируется как Basic auth
    }
    
    payload = {'scope': 'GIGACHAT_API_PERS'}
    
    try:
        response = requests.post(url, headers=headers, data=payload, verify=False)
        response.raise_for_status()
        data = response.json()
        
        access_token = data.get('access_token')
        expires_in = data.get('expires_in', 'unknown')
        
        print(f"✓ Access token получен (действует {expires_in} секунд)")
        print(f"  Token: {access_token[:50]}...{access_token[-10:]}")
        
        return access_token
    except Exception as e:
        print(f"✗ Ошибка при получении Access token: {e}")
        return None


def get_models(access_token: str) -> Optional[dict]:
    """
    Шаг 2: Отправить запрос к GigaChat API используя Access token
    
    Это эквивалентно: GET /api/v1/models/ с Bearer Authorization
    """
    url = "https://gigachat.devices.sberbank.ru/api/v1/models/"
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}',  # Access token используется как Bearer token
    }
    
    try:
        response = requests.get(url, headers=headers, verify=False)
        response.raise_for_status()
        data = response.json()
        
        print(f"✓ Список моделей получен")
        return data
    except Exception as e:
        print(f"✗ Ошибка при получении списка моделей: {e}")
        return None


def main():
    print("=" * 60)
    print("GigaChat API: Пример авторизации")
    print("=" * 60)
    print()
    
    # Получить Authorization key из переменной окружения
    auth_key = os.getenv('GIGACHAT_CREDENTIALS')
    
    if not auth_key:
        print("❌ Переменная GIGACHAT_CREDENTIALS не установлена")
        print()
        print("Инструкции:")
        print("1. Получите Authorization key с https://developers.sber.ru")
        print("2. Добавьте в .env файл: GIGACHAT_CREDENTIALS=your-key")
        print("3. Выполните: export $(cat .env | grep -v '#' | xargs)")
        print("4. Запустите этот скрипт снова")
        return
    
    print("Процесс авторизации:")
    print()
    
    # Шаг 1: Получить Access token
    print("1️⃣  Получение Access token...")
    access_token = get_access_token(auth_key)
    
    if not access_token:
        return
    
    print()
    
    # Шаг 2: Использовать Access token
    print("2️⃣  Отправка запроса к API...")
    models_data = get_models(access_token)
    
    if not models_data:
        return
    
    print()
    print("=" * 60)
    print("Результаты:")
    print("=" * 60)
    print()
    
    if 'data' in models_data:
        print(f"Доступные модели ({len(models_data['data'])} шт):")
        for model in models_data['data']:
            print(f"  - {model.get('id', 'unknown')} (от {model.get('owned_by', 'unknown')})")
    else:
        print(json.dumps(models_data, indent=2, ensure_ascii=False))
    
    print()
    print("=" * 60)
    print("✓ Авторизация работает корректно!")
    print("=" * 60)


if __name__ == '__main__':
    # Отключить предупреждения SSL (только для демонстрации)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    main()
