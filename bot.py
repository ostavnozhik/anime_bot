import os
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import asyncio
import json

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command('start'))
async def start_command(message: Message):
    await message.reply("Привет! Отправь мне скриншот из аниме.")

@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message):
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', file_bytes, filename='screenshot.jpg')
            async with session.post('https://api.trace.moe/search', data=data) as resp:
                # Получаем JSON-ответ
                result = await resp.json()

        # --- Диагностика (будет видно в логах Render) ---
        print(f"Ответ от trace.moe: {json.dumps(result, indent=2)}")

        # Проверяем, что ответ содержит поле 'result' и оно является списком
        if not isinstance(result.get('result'), list):
            await message.reply("⚠️ Сервис распознавания вернул неожиданный ответ. Попробуй позже.")
            return

        if len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        best = result['result'][0]

        # Убедимся, что best — это словарь с нужными ключами
        if not isinstance(best, dict):
            await message.reply("⚠️ Ошибка: неверный формат данных от сервиса.")
            return

        # Безопасно извлекаем данные
        anilist = best.get('anilist')
        if anilist and isinstance(anilist, dict):
            title = anilist.get('title', {})
            title_romaji = title.get('romaji') if isinstance(title, dict) else None
            title_english = title.get('english') if isinstance(title, dict) else None
            title_native = title.get('native') if isinstance(title, dict) else None
            title = title_romaji or title_english or title_native or best.get('filename', 'Неизвестно')
        else:
            title = best.get('filename', 'Неизвестно')

        episode = best.get('episode', 'неизвестно')
        from_time = best.get('from', 0.0)
        similarity = best.get('similarity', 0.0) * 100

        answer = (
            f"✅ Найдено!\n"
            f"📺 Название: {title}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {from_time:.1f} сек.\n"
            f"🎯 Точность: {similarity:.2f}%"
        )
        await message.reply(answer)

    except Exception as e:
        # Логируем ошибку в консоль Render
        print(f"Ошибка в handle_photo: {e}")
        await message.reply("⚠️ Произошла внутренняя ошибка. Попробуй позже.")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
