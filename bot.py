import os
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import asyncio

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
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)

    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field('image', file_bytes, filename='screenshot.jpg')
        async with session.post('https://api.trace.moe/search', data=data) as resp:
            result = await resp.json()

    if result.get('result'):
        best = result['result'][0]
        title = best['anilist']['title']['romaji'] or best['filename']
        episode = best.get('episode', 'неизвестно')
        from_time = best['from']
        similarity = best['similarity'] * 100

        await message.reply(
            f"✅ Найдено!\n"
            f"📺 Название: {title}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {from_time:.1f} сек.\n"
            f"🎯 Точность: {similarity:.2f}%"
        )
    else:
        await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
