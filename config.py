import os
from dataclasses import dataclass
from typing import Set

from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    # Telegram
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids_raw: str = os.getenv("ADMIN_IDS", "")

    # DeepSeek
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # SuperJob
    superjob_api_key: str = os.getenv("SUPERJOB_API_KEY", "")
    superjob_client_secret: str = os.getenv("SUPERJOB_CLIENT_SECRET", "")
    superjob_base_url: str = os.getenv("SUPERJOB_BASE_URL", "https://api.superjob.ru/2.0")

    # HeadHunter (hh.ru)
    hh_api_token: str = os.getenv("HH_API_TOKEN", "")
    hh_client_id: str = os.getenv("HH_CLIENT_ID", "")
    hh_client_secret: str = os.getenv("HH_CLIENT_SECRET", "")
    hh_redirect_uri: str = os.getenv("HH_REDIRECT_URI", "")
    hh_base_url: str = os.getenv("HH_BASE_URL", "https://api.hh.ru")

    # Habr Career
    habr_client_id: str = os.getenv("HABR_CLIENT_ID", "")
    habr_client_secret: str = os.getenv("HABR_CLIENT_SECRET", "")
    habr_base_url: str = "https://api.hh.ru"  # Habr использует API hh.ru

    # Avito
    avito_token: str = os.getenv("AVITO_TOKEN", "")
    avito_base_url: str = os.getenv("AVITO_BASE_URL", "https://api.avito.ru")

    # === VK (ВКОНТАКТЕ) ===
    vk_token: str = os.getenv("VK_TOKEN", "")
    """Токен доступа сообщества (создается в настройках группы ВК)"""
    
    vk_group_id_raw: str = os.getenv("VK_GROUP_ID", "")
    """ID группы ВКонтакте (число, например 236770128)"""
    
    vk_api_version: str = os.getenv("VK_API_VERSION", "5.199")
    """Версия VK API"""

    # === ЯНДЕКС.КАЛЕНДАРЬ ===
    yandex_login: str = os.getenv("YANDEX_LOGIN", "")
    """Логин от Яндекса (полный email, например: user@yandex.ru)"""
    
    yandex_app_password: str = os.getenv("YANDEX_APP_PASSWORD", "")
    """Пароль приложения, созданный в настройках Яндекса"""

    # Интеграция с календарём
    calendar_webhook_url: str = os.getenv("CALENDAR_WEBHOOK_URL", "")

    # === EMAIL НАСТРОЙКИ (ДЛЯ УВЕДОМЛЕНИЙ) ===
    smtp_server: str = os.getenv("SMTP_SERVER", "smtp.yandex.ru")
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    from_email: str = os.getenv("FROM_EMAIL", "")
    
    # Флаг для отключения проверки SSL (для отладки)
    smtp_verify_cert: bool = os.getenv("SMTP_VERIFY_CERT", "true").lower() == "true"

    # База данных
    db_url: str = os.getenv("DATABASE_URL", "sqlite:///./gwork.db")

    @property
    def admin_ids(self) -> Set[int]:
        """Парсит строку ADMIN_IDS в множество целых чисел"""
        if not self.admin_ids_raw:
            return set()
        return {
            int(x.strip()) for x in self.admin_ids_raw.split(",") 
            if x.strip().isdigit()
        }

    @property
    def vk_group_id(self) -> int:
        """Возвращает ID группы VK как целое число"""
        if not self.vk_group_id_raw:
            return 0
        try:
            return int(self.vk_group_id_raw.strip())
        except ValueError:
            return 0

    @property
    def has_vk(self) -> bool:
        """Проверяет, настроен ли VK бот"""
        return bool(self.vk_token) and self.vk_group_id > 0


settings = Settings()

# Для отладки - выведем какие токены загружены
print("=" * 60)
print("🔧 ЗАГРУЖЕННЫЕ НАСТРОЙКИ:")
print(f"✅ BOT_TOKEN: {'установлен' if settings.bot_token else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ HH_API_TOKEN: {'установлен' if settings.hh_api_token else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ SUPERJOB_API_KEY: {'установлен' if settings.superjob_api_key else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ HABR_CLIENT_ID: {'установлен' if settings.habr_client_id else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ DEEPSEEK_API_KEY: {'установлен' if settings.deepseek_api_key else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ VK_TOKEN: {'установлен' if settings.vk_token else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ VK_GROUP_ID: {settings.vk_group_id if settings.vk_group_id else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ YANDEX_LOGIN: {'установлен' if settings.yandex_login else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ YANDEX_APP_PASSWORD: {'установлен' if settings.yandex_app_password else 'НЕ УСТАНОВЛЕН'}")
print(f"✅ SMTP: {'настроен' if settings.smtp_username and settings.smtp_password else 'НЕ НАСТРОЕН'}")
print(f"✅ ADMIN_IDS: {settings.admin_ids}")
print("=" * 60)
