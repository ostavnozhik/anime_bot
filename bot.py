import os
import asyncio
import json
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан")
    exit(1)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

class SearchState(StatesGroup):
    results = State()
    index = State()

start_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Старт", callback_data="start")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ]
)

next_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="❌ Нет, ищи другое", callback_data="next")]
    ]
)

async def get_russian_title(anilist_id: int) -> str:
    """Возвращает русское название аниме по ID из AniList, либо romaji, если русского нет."""
    query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        title {
          romaji
          english
          native
        }
      }
    }
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "query": query,
        "variables": {"id": anilist_id}
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://graphql.anilist.co", json=payload, headers=headers) as resp:
            data = await resp.json()
            media = data.get("data", {}).get("Media")
            if not media:
                return None
            titles = media.get("title", {})
            return titles.get("romaji") or titles.get("english") or titles.get("native")

def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes} мин {secs} сек"

# --- Хендлеры бота ---
@dp.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "Привет! Я помогу найти аниме по скриншоту.\n"
        "Нажми кнопку «Старт», чтобы начать.",
        reply_markup=start_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    await message.reply(
        "🤖 Как я работаю:\n"
        "1. Нажми «Старт» или отправь /start\n"
        "2. Пришли скриншот из аниме\n"
        "3. Я найду несколько вариантов (если есть)\n"
        "4. Нажимай «Нет, ищи другое», чтобы переключать результаты\n"
        "Если ничего не найдено — попробуй другой скриншот."
    )

@dp.callback_query(lambda c: c.data == "start")
async def process_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "Отправь мне скриншот из аниме, и я попробую найти его источник."
    )
    await callback.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🤖 Я умею искать аниме по скриншоту.\n"
        "Просто отправь мне фото, и я найду источник.\n"
        "Если найдено несколько вариантов — нажимай «Нет, ищи другое» для переключения.\n"
        "Всегда можно начать заново через /start."
    )

@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', file_bytes, filename='screenshot.jpg')
            async with session.post('https://api.trace.moe/search', data=data) as resp:
                result = await resp.json()

        print(f"Ответ trace.moe: {json.dumps(result, indent=2)}")

        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        results = result['result']
        for item in results:
            anilist_id = item.get('anilist', {}).get('id')
            if anilist_id:
                russian_title = await get_russian_title(anilist_id)
                if russian_title:
                    item['russian_title'] = russian_title
                else:
                    item['russian_title'] = item.get('anilist', {}).get('title', {}).get('romaji', item.get('filename', 'Неизвестно'))
            else:
                item['russian_title'] = item.get('filename', 'Неизвестно')

        await state.update_data(results=results, index=0)
        await show_result(message, state, 0)

    except Exception as e:
        print(f"Ошибка в handle_photo: {e}")
        await message.reply("⚠️ Произошла ошибка. Попробуй позже.")

async def show_result(message: Message, state: FSMContext, idx: int):
    data = await state.get_data()
    results = data.get('results')
    if not results or idx >= len(results):
        await message.reply("🏁 Это был последний результат. Попробуй другой скриншот.")
        await state.clear()
        return

    best = results[idx]

    title = best.get('russian_title', 'Неизвестно')

    episode = best.get('episode', 'неизвестно')
    from_time = best.get('from', 0.0)
    similarity = best.get('similarity', 0.0) * 100

    time_str = format_time(from_time)

    answer = (
        f"✅ Найдено!\n"
        f"📺 Название: {title}\n"
        f"🎬 Эпизод: {episode}\n"
        f"⏱ Время: {time_str}\n"
        f"🎯 Точность: {similarity:.2f}%\n"
        f"({idx+1}/{len(results)})"
    )

    await message.reply(answer, reply_markup=next_kb)
    await state.update_data(index=idx)

@dp.callback_query(lambda c: c.data == "next")
async def process_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    results = data.get('results')
    idx = data.get('index', 0)

    if not results:
        await callback.message.edit_text("⚠️ Результаты не найдены. Попробуй сначала.")
        await state.clear()
        return

    next_idx = idx + 1
    if next_idx >= len(results):
        await callback.message.edit_text("🏁 Это был последний результат. Попробуй другой скриншот.")
        await state.clear()
        return

    await show_result(callback.message, state, next_idx)
    await callback.message.delete()

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
    print(f"✅ Веб-сервер health-check запущен на порту {port}")
    await asyncio.Event().wait()

async def main():
    await asyncio.gather(
        dp.start_polling(bot),
        start_web_server()
    )

if __name__ == '__main__':
    asyncio.run(main())
