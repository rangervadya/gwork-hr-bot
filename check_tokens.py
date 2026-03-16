import httpx
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_hh_token():
    """Проверка HH токена"""
    token = os.getenv("HH_API_TOKEN")
    if not token:
        print("❌ HH_API_TOKEN не найден в .env")
        return
    
    print("\n🔍 ПРОВЕРКА HEADHUNTER ТОКЕНА")
    print("=" * 50)
    
    async with httpx.AsyncClient() as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "GWorkBot/1.0"
        }
        
        # Тест 1: Проверка самого токена
        print("📡 Тест 1: Получение информации о пользователе...")
        response = await client.get("https://api.hh.ru/me", headers=headers)
        
        if response.status_code == 200:
            user = response.json()
            print(f"✅ Токен работает!")
            print(f"   👤 Имя: {user.get('first_name')} {user.get('last_name')}")
            print(f"   📧 Email: {user.get('email')}")
            print(f"   🔑 Тип: {user.get('type', 'не указан')}")
        else:
            print(f"❌ Ошибка {response.status_code}")
            print(f"   {response.text}")
            return
        
        # Тест 2: Проверка доступа к резюме
        print("\n📡 Тест 2: Проверка доступа к резюме...")
        resume_response = await client.get(
            "https://api.hh.ru/resumes/mine",
            headers=headers
        )
        
        if resume_response.status_code == 200:
            resumes = resume_response.json()
            print(f"✅ Есть доступ к резюме! (работодатель)")
            print(f"   📄 Найдено резюме: {len(resumes.get('items', []))}")
            return True
        elif resume_response.status_code == 403:
            print(f"❌ Нет доступа к резюме (обычный пользователь)")
            print(f"   Будет использован поиск вакансий")
            return False
        else:
            print(f"❌ Ошибка {resume_response.status_code}")
            print(f"   {resume_response.text}")
            return False


async def check_superjob_token():
    """Проверка SuperJob токена"""
    token = os.getenv("SUPERJOB_API_KEY")
    if not token:
        print("❌ SUPERJOB_API_KEY не найден в .env")
        return
    
    print("\n🔍 ПРОВЕРКА SUPERJOB ТОКЕНА")
    print("=" * 50)
    
    async with httpx.AsyncClient() as client:
        headers = {
            "X-Api-App-Id": token,
            "User-Agent": "GWorkBot/1.0"
        }
        
        # Тест: Поиск резюме
        print("📡 Поиск резюме...")
        response = await client.get(
            "https://api.superjob.ru/2.0/resumes/",
            params={
                "keyword": "администратор",
                "count": 1
            },
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            total = data.get('total', 0)
            print(f"✅ Токен работает!")
            print(f"   📄 Всего резюме в базе: {total}")
            print(f"   🟢 Доступ к резюме есть!")
            return True
        else:
            print(f"❌ Ошибка {response.status_code}")
            print(f"   {response.text}")
            return False


async def check_all():
    """Проверка всех токенов"""
    print("🚀 ПРОВЕРКА ТОКЕНОВ ДЛЯ GWORK BOT")
    print("=" * 60)
    
    # Читаем .env файл
    print("\n📁 Содержимое .env:")
    with open('.env', 'r') as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                key = line.split('=')[0].strip()
                value = line.split('=')[1].strip()
                masked = value[:5] + '*' * (len(value)-5) if len(value) > 5 else '***'
                print(f"   {key}: {masked}")
    
    # Проверяем HH
    hh_result = await check_hh_token()
    
    # Проверяем SuperJob
    sj_result = await check_superjob_token()
    
    print("\n" + "=" * 60)
    print("📊 ИТОГ:")
    print(f"HeadHunter: {'✅ Есть доступ к резюме' if hh_result else '❌ Нет доступа к резюме'}")
    print(f"SuperJob: {'✅ Работает' if sj_result else '❌ Не работает'}")
    print("=" * 60)
    
    return hh_result, sj_result

if __name__ == "__main__":
    asyncio.run(check_all())