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
import logging

import db

db.init_db()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не задан")
    sys.exit(1)

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

@dp.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    user = message.from_user
    db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    await state.clear()
    await message.reply(
        "Привет! Отправь мне скриншот из аниме.\n"
        "Или нажми кнопку «Старт».",
        reply_markup=start_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    await message.reply(
        "🤖 Как я работаю:\n1. Пришли скриншот\n2. Я найду варианты\n3. Нажимай «Нет, ищи другое» для переключения\nЕсли что-то сломалось, отправь /start заново."
    )

@dp.message(Command('clear'))
async def clear_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply("🧹 Состояние сброшено. Начни заново через /start.")

@dp.callback_query(lambda c: c.data == "start")
async def process_start(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        await callback.message.edit_text("Отправь мне скриншот из аниме.", reply_markup=None)
    except Exception as e:
        logger.error(f"process_start: {e}")
        await callback.message.answer("⚠️ Ошибка, попробуй /start")

@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    try:
        await callback.answer()
        await callback.message.answer(
            "🤖 Отправь скриншот, я найду аниме.\nЕсли результат не тот, нажимай «Нет, ищи другое».\nДля сброса используй /clear"
        )
    except Exception as e:
        logger.error(f"process_help: {e}")

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

        logger.info(f"trace.moe ответ: {json.dumps(result, indent=2)[:300]}")
        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return
        await state.update_data(results=result['result'], index=0)
        await show_result(message, state, 0)
    except asyncio.TimeoutError:
        await message.reply("⏳ Сервис поиска долго отвечает. Попробуй позже.")
    except Exception as e:
        logger.error(f"handle_photo: {traceback.format_exc()}")
        await message.reply("⚠️ Что-то пошло не так. Попробуй другой скриншот или /start.")

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
        logger.error(f"show_result: {e}")
        await message.reply("⚠️ Ошибка отображения результата. Попробуй /start")

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
        logger.error(f"process_next: {e}")
        await callback.message.answer("⚠️ Ошибка, попробуй /start")

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
    logger.info(f"✅ Веб-сервер health-check запущен на порту {port}")
    await asyncio.Event().wait()

async def watchdog():
    """Проверяет соединение с Telegram каждые 30 секунд, если бот не отвечает — инициирует перезапуск."""
    while True:
        await asyncio.sleep(30)
        try:
            await bot.get_me()
            logger.debug("Watchdog: соединение с Telegram работает")
        except Exception as e:
            logger.error(f"Watchdog: соединение потеряно ({e}), инициируем перезапуск поллинга")
            await bot.session.close()
            raise RuntimeError("Watchdog инициировал перезапуск")

async def bot_runner():
    while True:
        try:
            logger.info("🚀 Запуск бота...")
            watchdog_task = asyncio.create_task(watchdog())
            await dp.start_polling(bot)
            watchdog_task.cancel()
            logger.warning("⚠️ Поллинг завершился, перезапуск через 2 секунды")
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            logger.info("Бот остановлен корректно")
            break
        except Exception as e:
            logger.error(f"❌ Бот упал: {e}\n{traceback.format_exc()}")
            logger.info("🔄 Перезапуск через 5 секунд...")
            await asyncio.sleep(5)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass

async def main():
    await asyncio.gather(
        bot_runner(),
        start_web_server()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
