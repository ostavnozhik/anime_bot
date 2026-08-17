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
import traceback
import sys

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан")
    sys.exit(1)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

class SearchState(StatesGroup):
    results = State()
    index = State()

# Клавиатуры
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

def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes} мин {secs} сек"

def extract_title(best: dict) -> str:
    try:
        anilist = best.get('anilist')
        if isinstance(anilist, dict):
            title = anilist.get('title', {})
            if isinstance(title, dict):
                return title.get('romaji') or title.get('english') or title.get('native') or best.get('filename', 'Неизвестно')
        return best.get('filename', 'Неизвестно')
    except Exception:
        return "Неизвестно"

# --- Команды ---
@dp.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "Привет! Отправь мне скриншот из аниме.\n"
        "Или нажми кнопку «Старт».",
        reply_markup=start_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    await message.reply(
        "🤖 Как я работаю:\n"
        "1. Пришли скриншот\n"
        "2. Я найду варианты\n"
        "3. Нажимай «Нет, ищи другое» для переключения\n"
        "Если что-то сломалось, отправь /start заново."
    )

@dp.message(Command('clear'))
async def clear_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply("🧹 Состояние сброшено. Начни заново через /start.")

# --- Кнопка "Старт" (исправлена) ---
@dp.callback_query(lambda c: c.data == "start")
async def process_start(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        await callback.message.edit_text(
            "Отправь мне скриншот из аниме.",
            reply_markup=None
        )
    except Exception as e:
        print(f"Ошибка в process_start: {e}")
        await callback.message.answer("⚠️ Ошибка, попробуй /start")

# --- Кнопка "Помощь" ---
@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    try:
        await callback.answer()
        await callback.message.answer(
            "🤖 Отправь скриншот, я найду аниме.\n"
            "Если результат не тот, нажимай «Нет, ищи другое».\n"
            "Для сброса используй /clear"
        )
    except Exception as e:
        print(f"Ошибка в process_help: {e}")

# --- Обработчик фото ---
@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            data = aiohttp.FormData()
            data.add_field('image', file_bytes, filename='screenshot.jpg')
            async with session.post('https://api.trace.moe/search', data=data) as resp:
                result = await resp.json()

        print(f"Ответ trace.moe: {json.dumps(result, indent=2)[:500]}")

        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        await state.update_data(results=result['result'], index=0)
        await show_result(message, state, 0)

    except asyncio.TimeoutError:
        await message.reply("⏳ Сервис поиска долго отвечает. Попробуй позже.")
    except Exception as e:
        print(f"Ошибка в handle_photo:\n{traceback.format_exc()}")
        await message.reply("⚠️ Что-то пошло не так. Попробуй другой скриншот или /start.")

# --- Функция показа результата ---
async def show_result(message: Message, state: FSMContext, idx: int):
    try:
        data = await state.get_data()
        results = data.get('results')
        if not results or idx >= len(results):
            await message.reply("🏁 Это был последний результат. Попробуй другой скриншот.")
            await state.clear()
            return

        best = results[idx]
        name = extract_title(best)
        episode = best.get('episode', 'неизвестно')
        from_time = best.get('from', 0.0)
        similarity = best.get('similarity', 0.0) * 100
        time_str = format_time(from_time)

        answer = (
            f"✅ Найдено!\n"
            f"📺 Название: {name}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {time_str}\n"
            f"🎯 Точность: {similarity:.2f}%\n"
            f"({idx+1}/{len(results)})"
        )

        await message.reply(answer, reply_markup=next_kb)
        await state.update_data(index=idx)

    except Exception as e:
        print(f"Ошибка в show_result: {e}")
        await message.reply("⚠️ Ошибка отображения результата. Попробуй /start")

# --- Кнопка "Нет, ищи другое" ---
@dp.callback_query(lambda c: c.data == "next")
async def process_next(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        data = await state.get_data()
        results = data.get('results')
        idx = data.get('index', 0)

        if not results:
            await callback.message.edit_text("⚠️ Результаты не найдены. Попробуй /start")
            await state.clear()
            return

        next_idx = idx + 1
        if next_idx >= len(results):
            await callback.message.edit_text("🏁 Это был последний результат. Попробуй другой скриншот.")
            await state.clear()
            return

        await show_result(callback.message, state, next_idx)
        try:
            await callback.message.delete()
        except Exception:
            pass

    except Exception as e:
        print(f"Ошибка в process_next: {e}")
        await callback.message.answer("⚠️ Ошибка, попробуй /start")

# --- Веб-сервер health-check ---
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

# --- Запуск ---
async def main():
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            start_web_server()
        )
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        await asyncio.sleep(5)
        os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == '__main__':
    asyncio.run(main())
