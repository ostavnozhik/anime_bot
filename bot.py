import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message

BOT_TOKEN = os.getenv('BOT_TOKEN')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message_handler(commands=['start'])
async def start_command(message: Message):
    await message.reply("Привет! Отправь мне скриншот из аниме, и я попробую найти его источник.")

@dp.message_handler(content_types=['photo'])
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

        answer = (
            f"✅ Найдено!\n"
            f"📺 Название: {title}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {from_time:.1f} сек.\n"
            f"🎯 Точность: {similarity:.2f}%"
        )
        await message.reply(answer)
    else:
        await message.reply("😔 Ничего не найдено. Попробуйте другой скриншот.")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
