import asyncio
import logging
import os
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from main import dp, bot  # импортируем вашего бота

load_dotenv()

app = Flask(__name__)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка вебхуков от Telegram"""
    try:
        update_data = request.get_json()
        logger.info(f"Получен update: {update_data.get('update_id')}")
        
        # Создаем цикл для обработки
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        update = types.Update(**update_data)
        loop.run_until_complete(dp.feed_update(bot, update))
        loop.close()
        
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return jsonify({'status': 'error'}), 500

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """Установка вебхука"""
    domain = request.host
    webhook_url = f"https://{domain}/webhook"
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot.set_webhook(webhook_url))
    loop.close()
    
    return f"✅ Webhook set to {webhook_url}"

@app.route('/')
def index():
    return "GWork HR Bot is running on PythonAnywhere!"

# Важно для WSGI
application = app