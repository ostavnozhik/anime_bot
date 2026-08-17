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

# --- Токен ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан")
    exit(1)

# --- Хранилище для FSM ---
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- Состояния FSM ---
class SearchState(StatesGroup):
    results = State()   # список результатов
    index = State()     # текущий индекс (0-based)

# --- Клавиатуры ---
start_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Старт", callback_data="start")]
    ]
)

next_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="❌ Нет, ищи другое", callback_data="next")]
    ]
)

# --- Хендлер команды /start ---
@dp.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "Привет! Я помогу найти аниме по скриншоту.\n"
        "Нажми кнопку «Старт», чтобы начать.",
        reply_markup=start_kb
    )

# --- Обработчик нажатия кнопки "Старт" ---
@dp.callback_query(lambda c: c.data == "start")
async def process_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "Отправь мне скриншот из аниме, и я попробую найти его источник."
    )
    # Убираем клавиатуру, так как теперь ждём фото
    await callback.message.edit_reply_markup(reply_markup=None)

# --- Хендлер фото ---
@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message, state: FSMContext):
    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)

        # Отправляем запрос к trace.moe
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', file_bytes, filename='screenshot.jpg')
            async with session.post('https://api.trace.moe/search', data=data) as resp:
                result = await resp.json()

        # Логируем ответ (для отладки)
        print(f"Ответ trace.moe: {json.dumps(result, indent=2)}")

        # Проверяем структуру ответа
        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        # Сохраняем результаты в FSM
        await state.update_data(results=result['result'], index=0)
        await show_result(message, state, 0)

    except Exception as e:
        print(f"Ошибка в handle_photo: {e}")
        await message.reply("⚠️ Произошла ошибка. Попробуй позже.")

# --- Функция показа результата по индексу ---
async def show_result(message: Message, state: FSMContext, idx: int):
    data = await state.get_data()
    results = data.get('results')
    if not results or idx >= len(results):
        await message.reply("🏁 Это был последний результат. Попробуй другой скриншот.")
        await state.clear()
        return

    best = results[idx]
    # Извлечение названия
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
        f"🎯 Точность: {similarity:.2f}%\n"
        f"({idx+1}/{len(results)})"
    )

    # Показываем результат с кнопкой "Нет, ищи другое"
    await message.reply(answer, reply_markup=next_kb)
    # Сохраняем текущий индекс в состоянии
    await state.update_data(index=idx)

# --- Обработчик кнопки "Нет, ищи другое" ---
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

    # Показываем следующий результат
    await show_result(callback.message, state, next_idx)
    # Удаляем предыдущее сообщение с кнопкой (по желанию)
    await callback.message.delete()

# --- Веб-сервер для health-check (чтобы Render не ругался) ---
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

# --- Запуск бота и сервера ---
async def main():
    await asyncio.gather(
        dp.start_polling(bot),
        start_web_server()
    )

if __name__ == '__main__':
    asyncio.run(main())
