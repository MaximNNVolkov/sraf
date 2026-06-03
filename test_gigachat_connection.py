#!/usr/bin/env python3
"""
Тест различных способов аутентификации GigaChat
"""

import os
import sys

def test_gigachat_connection():
    """Попытаться подключиться к GigaChat с текущими credentials"""
    
    credentials = os.getenv('GIGACHAT_CREDENTIALS')
    
    if not credentials:
        print("❌ GIGACHAT_CREDENTIALS не установлена")
        return False
    
    print(f"Тестирование с credentials: {credentials[:20]}...")
    print()
    
    try:
        from gigachat import GigaChat
        
        # Попытка 1: Использовать credentials как Authorization key
        print("1️⃣  Попытка подключиться...")
        with GigaChat(credentials=credentials) as client:
            print("   Получение доступного token...")
            token = client.get_token()
            print(f"   ✓ Token получен: {str(token)[:50]}...")
            print(f"   ✓ Подключение работает!")
            return True
            
    except Exception as e:
        error_msg = str(e)
        print(f"   ✗ Ошибка: {error_msg[:100]}...")
        print()
        
        # Проанализировать ошибку
        if "Invalid" in error_msg or "invalid" in error_msg:
            print("💡 Подсказка: Похоже, что credentials имеют неправильный формат")
            print("   Client ID (UUID) не может быть использован напрямую как Authorization key")
            print("   Нужен Authorization key в Base64 формате")
            return False
        elif "401" in error_msg or "Unauthorized" in error_msg:
            print("💡 Подсказка: Ошибка авторизации")
            print("   Проверьте, что значение скопировано полностью и без пробелов")
            return False
        else:
            print(f"💡 Полная ошибка: {error_msg}")
            return False

def show_instructions():
    """Показать инструкции что делать"""
    print()
    print("=" * 70)
    print("ИНСТРУКЦИИ")
    print("=" * 70)
    print()
    print("На портале разработчика https://developers.sber.ru вы должны получить:")
    print()
    print("1️⃣  Authorization key (или API key)")
    print("   - Это строка в формате Base64")
    print("   - Выглядит так:")
    print("     MGU5NjI4YmItNGY1YS00YWFjLThhYjAtZTJhMjU0YmRjMDQ0OjIyZTA2OTg1LWUwYTctNDcwZC1iODc1LTliOGNlYmE0MGJmNg==")
    print()
    print("2️⃣  Если вы получили Client ID + Client Secret:")
    print("   - Client ID: 661edafe-d476-447e-b484-fe5c34f0e1d2 (UUID)")
    print("   - Client Secret: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
    print("   → Нужно закодировать их в Base64 как: base64(client_id:client_secret)")
    print()
    print("Что вы получили:")
    creds = os.getenv('GIGACHAT_CREDENTIALS', 'НЕ УСТАНОВЛЕНО')
    print(f"   GIGACHAT_CREDENTIALS={creds}")
    print()
    print("=" * 70)

if __name__ == '__main__':
    print()
    print("🔍 Проверка подключения к GigaChat")
    print()
    
    success = test_gigachat_connection()
    
    if not success:
        show_instructions()
    
    sys.exit(0 if success else 1)
