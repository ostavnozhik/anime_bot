import os
import asyncio
import json
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

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
                result = await resp.json()

        print(f"Ответ от trace.moe: {json.dumps(result, indent=2)}")

        if not isinstance(result.get('result'), list):
            await message.reply("⚠️ Сервис распознавания вернул неожиданный ответ. Попробуй позже.")
            return

        if len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        best = result['result'][0]
        if not isinstance(best, dict):
            await message.reply("⚠️ Ошибка: неверный формат данных от сервиса.")
            return

        anilist = best.get('anilist')
        if anilist and isinstance(anilist, dict):
            title_obj = anilist.get('title', {})
            title = title_obj.get('romaji') or title_obj.get('english') or title_obj.get('native') or best.get('filename', 'Неизвестно')
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
        print(f"Ошибка в handle_photo: {e}")
        await message.reply("⚠️ Произошла внутренняя ошибка. Попробуй позже.")

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    port = int(os.getenv('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ Веб-сервер для health-check запущен на порту {port}")
    await asyncio.Event().wait() 

async def main():
    await asyncio.gather(
        dp.start_polling(bot),
        start_web_server()
    )

if __name__ == '__main__':
    asyncio.run(main())
