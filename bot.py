import os
import asyncio
import json
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import traceback
import sys
import hashlib
import time
from collections import defaultdict
from PIL import Image
import io
import cv2
import tempfile

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
    video_bytes = State()
    search_count = State()

# --- Кеш ---
search_cache = {}
CACHE_TTL = 3600

def get_cache_key(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

async def get_cached_result(key: str):
    if key in search_cache:
        result, timestamp = search_cache[key]
        if time.time() - timestamp < CACHE_TTL:
            return result
        else:
            del search_cache[key]
    return None

def cache_result(key: str, result):
    search_cache[key] = (result, time.time())

# --- Троттлинг ---
user_last_request = defaultdict(float)
REQUEST_INTERVAL = 3

# --- Клавиатуры ---
help_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ]
)

next_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="❌ Нет, ищи другое", callback_data="next")]
    ]
)

# --- Вспомогательные функции ---
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

def compress_image(image_bytes: bytes, max_size: int = 800) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        return image_bytes

def extract_frames(video_path: str, percentages: list) -> list:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("Не удалось открыть видео")
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames == 0:
        raise Exception("Видео не содержит кадров")
    frames = []
    for pct in percentages:
        frame_pos = int(total_frames * (pct / 100.0))
        frame_pos = max(0, min(frame_pos, total_frames - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
        ret, frame = cap.read()
        if ret:
            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frames.append(jpeg.tobytes())
    cap.release()
    return frames

async def search_by_frame(image_bytes: bytes) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        data = aiohttp.FormData()
        data.add_field('image', image_bytes, filename='frame.jpg')
        async with session.post('https://api.trace.moe/search', data=data) as resp:
            return await resp.json()

# --- Установка команд меню ---
async def set_default_commands():
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="help", description="❓ Помощь"),
    ]
    await bot.set_my_commands(commands)
    print("✅ Меню команд установлено")

# --- Команды ---
@dp.message(Command('start'))
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "👋 Привет! Отправь скриншот или видео, и я найду аниме.\n"
        "Для видео — 5 кадров, если не подойдёт, нажми «Нет, ищи другое» для новых.\n\n"
        "❓ Помощь — /help",
        reply_markup=help_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    await message.reply(
        "🤖 **Бот для поиска аниме по кадру**\n\n"
        "📸 Отправьте скриншот или видео — я найду тайтл.\n"
        "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
        "🔗 В ответе даю ссылку на Shikimori.\n\n"
        "Команды:\n"
        "/start — начать заново\n"
        "/help — эта справка"
    )

@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    try:
        await callback.answer()
        await callback.message.answer(
            "🤖 **Бот для поиска аниме по кадру**\n\n"
            "📸 Отправьте скриншот или видео — я найду тайтл.\n"
            "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
            "🔗 В ответе даю ссылку на Shikimori.\n\n"
            "Команды:\n"
            "/start — начать заново\n"
            "/help — эта справка"
        )
    except Exception as e:
        print(f"Ошибка в process_help: {e}")
    except Exception as e:
        print(f"Ошибка в process_help: {e}")

# --- Обработчик фото ---
@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        now = time.time()
        if now - user_last_request[user_id] < REQUEST_INTERVAL:
            await message.reply("⏳ Подожди немного.")
            return
        user_last_request[user_id] = now

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        raw_bytes = file_bytes.getvalue()

        cache_key = get_cache_key(raw_bytes)
        cached = await get_cached_result(cache_key)
        if cached:
            await state.update_data(results=cached['result'], index=0, video_bytes=None, search_count=0)
            await show_result(message, state, 0)
            return

        compressed = compress_image(raw_bytes)
        result = await search_by_frame(compressed)

        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено.")
            return

        cache_result(cache_key, {'result': result['result']})
        await state.update_data(results=result['result'], index=0, video_bytes=None, search_count=0)
        await show_result(message, state, 0)

    except asyncio.TimeoutError:
        await message.reply("⏳ Сервис долго отвечает.")
    except Exception as e:
        print(f"Ошибка handle_photo:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой скриншот или /start")

# --- Обработчик видео ---
@dp.message(lambda msg: msg.video is not None)
async def handle_video(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        now = time.time()
        if now - user_last_request[user_id] < REQUEST_INTERVAL:
            await message.reply("⏳ Подожди немного.")
            return
        user_last_request[user_id] = now

        video = message.video
        file = await bot.get_file(video.file_id)
        file_bytes = await bot.download_file(file.file_path)
        raw_bytes = file_bytes.getvalue()

        cache_key = get_cache_key(raw_bytes)
        cached = await get_cached_result(cache_key)
        if cached:
            await state.update_data(results=cached['result'], index=0, video_bytes=raw_bytes, search_count=0)
            await show_result(message, state, 0)
            return

        percentages_first = [5, 20, 50, 70, 95]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(raw_bytes)
            video_path = tmp_file.name

        frames = extract_frames(video_path, percentages_first)
        os.unlink(video_path)

        if not frames:
            await message.reply("⚠️ Не удалось извлечь кадры.")
            return

        found_result = None
        for frame_bytes in frames:
            compressed = compress_image(frame_bytes)
            result = await search_by_frame(compressed)
            if result.get('result') and len(result['result']) > 0:
                found_result = result
                break
            await asyncio.sleep(0.5)

        if not found_result:
            await message.reply("😔 По видео ничего не найдено.")
            return

        cache_result(cache_key, {'result': found_result['result']})
        await state.update_data(results=found_result['result'], index=0, video_bytes=raw_bytes, search_count=0)
        await show_result(message, state, 0)

    except Exception as e:
        print(f"Ошибка handle_video:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой файл или /start")

# --- Функция показа результата со ссылкой на Shikimori ---
async def show_result(message: Message, state: FSMContext, idx: int):
    try:
        data = await state.get_data()
        results = data.get('results')
        if not results or idx >= len(results):
            await message.reply("🏁 Это был последний результат. Попробуй другой файл.")
            await state.clear()
            return

        best = results[idx]
        name = extract_title(best)
        episode = best.get('episode', 'неизвестно')
        from_time = best.get('from', 0.0)
        similarity = best.get('similarity', 0.0) * 100
        time_str = format_time(from_time)

        anilist_id = best.get('anilist', {}).get('id')
        shikimori_url = f"https://shikimori.one/animes/{anilist_id}" if anilist_id else None

        answer = (
            f"✅ Найдено!\n"
            f"📺 Название: {name}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {time_str}\n"
            f"🎯 Точность: {similarity:.2f}%\n"
            f"({idx+1}/{len(results)})"
        )
        if shikimori_url:
            answer += f"\n\n🔗 [Смотреть на Shikimori]({shikimori_url})"

        await message.reply(answer, reply_markup=next_kb)
        await state.update_data(index=idx)

    except Exception as e:
        print(f"Ошибка show_result: {e}")
        await message.reply("⚠️ Ошибка, попробуй /start")

# --- Обработчик кнопки "Нет, ищи другое" ---
@dp.callback_query(lambda c: c.data == "next")
async def process_next(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        data = await state.get_data()
        results = data.get('results')
        idx = data.get('index', 0)
        video_bytes = data.get('video_bytes')
        search_count = data.get('search_count', 0)

        if not results:
            await callback.message.edit_text("⚠️ Результаты не найдены. Попробуй /start")
            await state.clear()
            return

        if video_bytes is not None and search_count == 0:
            percentages_second = [8, 22, 53, 75, 90]
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_file.write(video_bytes)
                video_path = tmp_file.name

            frames = extract_frames(video_path, percentages_second)
            os.unlink(video_path)

            if not frames:
                await callback.message.edit_text("⚠️ Не удалось извлечь кадры.")
                return

            found_result = None
            for frame_bytes in frames:
                compressed = compress_image(frame_bytes)
                result = await search_by_frame(compressed)
                if result.get('result') and len(result['result']) > 0:
                    found_result = result
                    break
                await asyncio.sleep(0.5)

            if not found_result:
                await callback.message.edit_text("😔 Второй набор кадров ничего не дал.")
                await state.clear()
                return

            await state.update_data(results=found_result['result'], index=0, search_count=1)
            await show_result(callback.message, state, 0)
            try:
                await callback.message.delete()
            except Exception:
                pass
            return

        next_idx = idx + 1
        if next_idx >= len(results):
            await callback.message.edit_text("🏁 Это был последний результат.")
            await state.clear()
            return

        await show_result(callback.message, state, next_idx)
        try:
            await callback.message.delete()
        except Exception:
            pass

    except Exception as e:
        print(f"Ошибка process_next: {e}")
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
    await set_default_commands()
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
