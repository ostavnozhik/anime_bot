import os
import asyncio
import json
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
import traceback
import sys
import hashlib
import time
from collections import defaultdict
import subprocess
import tempfile
from PIL import Image
import io

sys.stdout.reconfigure(line_buffering=True)

print("🚀 БОТ ЗАПУСКАЕТСЯ...")

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан")
    sys.exit(1)

print("✅ Токен получен")

try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    print("✅ FFmpeg доступен")
except Exception:
    print("❌ FFmpeg НЕ ДОСТУПЕН")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Ручное хранилище данных пользователей ---
user_data = {}  # {user_id: {'results': [...], 'index': 0, 'video_bytes': b'...', 'search_count': 0}}

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

# --- Вспомогательные ---
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
    # Для теста сжатие отключено
    print(f"   🖼️ Размер фото: {len(image_bytes)} байт")
    return image_bytes

def extract_frames(video_path: str, percentages: list) -> list:
    print(f"   🎬 Извлечение кадров на {percentages}%")
    cmd_duration = [
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path
    ]
    try:
        duration = float(subprocess.check_output(cmd_duration, text=True).strip())
        print(f"   ⏱️ Длительность: {duration} сек")
    except Exception as e:
        print(f"   ❌ Ошибка длительности: {e}")
        raise Exception("Не удалось получить длительность видео")

    frames = []
    for pct in percentages:
        time_sec = duration * (pct / 100.0)
        cmd = [
            'ffmpeg', '-ss', str(time_sec), '-i', video_path,
            '-vframes', '1', '-f', 'image2pipe', '-'
        ]
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=10)
            if process.returncode == 0 and stdout:
                frames.append(stdout)
                print(f"   ✅ Кадр на {pct}% извлечён, {len(stdout)} байт")
            else:
                print(f"   ⚠️ Ошибка кадра на {pct}%: {stderr.decode()[:100]}")
        except Exception as e:
            print(f"   ❌ ffmpeg ошибка: {e}")
    return frames

async def search_by_frame(image_bytes: bytes) -> dict:
    print(f"   🔍 Запрос к trace.moe, размер {len(image_bytes)} байт")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        data = aiohttp.FormData()
        data.add_field('image', image_bytes, filename='frame.jpg')
        async with session.post('https://api.trace.moe/search', data=data) as resp:
            result = await resp.json()
            print(f"   📨 Ответ trace.moe: {json.dumps(result, indent=2, ensure_ascii=False)[:1000]}")
            return result

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
async def start_command(message: Message):
    user_id = message.from_user.id
    user_data.pop(user_id, None)  # очищаем данные пользователя
    print(f"📩 /start от {user_id}")
    await message.reply(
        "👋 Привет! Отправь скриншот или видео, и я найду аниме.\n\n❓ Помощь — /help",
        reply_markup=help_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    print(f"📩 /help от {message.from_user.id}")
    await message.reply(
        "🤖 **Бот для поиска аниме по кадру**\n\n"
        "📸 Отправьте скриншот или видео — я найду тайтл.\n"
        "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
        "🔗 В ответе даю ссылку на Shikimori.\n\n"
        "Команды:\n/start — начать заново\n/help — эта справка"
    )

@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    print(f"📩 Кнопка помощи от {callback.from_user.id}")
    try:
        await callback.answer()
        await callback.message.answer(
            "🤖 **Бот для поиска аниме по кадру**\n\n"
            "📸 Отправьте скриншот или видео — я найду тайтл.\n"
            "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
            "🔗 В ответе даю ссылку на Shikimori."
        )
    except Exception as e:
        print(f"❌ Ошибка process_help: {e}")

# --- Обработчик фото ---
@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    print(f"📸 Обработка фото от {user_id}")
    try:
        now = time.time()
        if now - user_last_request[user_id] < REQUEST_INTERVAL:
            await message.reply("⏳ Подожди немного.")
            return
        user_last_request[user_id] = now

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        raw_bytes = file_bytes.getvalue()
        print(f"   📥 Фото скачано, размер {len(raw_bytes)} байт")

        cache_key = get_cache_key(raw_bytes)
        cached = await get_cached_result(cache_key)
        if cached:
            print("   ♻️ Используем кеш")
            user_data[user_id] = {
                'results': cached['result'],
                'index': 0,
                'video_bytes': None,
                'search_count': 0
            }
            await show_result(message, user_id)
            return

        compressed = compress_image(raw_bytes)
        result = await search_by_frame(compressed)

        if result.get('error'):
            await message.reply(f"⚠️ Ошибка API: {result['error']}")
            return

        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        results_list = result['result']
        if not results_list or not isinstance(results_list[0], dict):
            await message.reply("⚠️ Неверный формат данных от сервиса.")
            return

        cache_result(cache_key, {'result': results_list})
        user_data[user_id] = {
            'results': results_list,
            'index': 0,
            'video_bytes': None,
            'search_count': 0
        }
        await show_result(message, user_id)

    except asyncio.TimeoutError:
        await message.reply("⏳ Сервис поиска долго отвечает.")
    except Exception as e:
        print(f"❌ Ошибка handle_photo:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой скриншот или /start")

# --- Обработчик видео ---
@dp.message(lambda msg: msg.video is not None)
async def handle_video(message: Message):
    user_id = message.from_user.id
    print(f"🎬 Обработка видео от {user_id}")
    try:
        now = time.time()
        if now - user_last_request[user_id] < REQUEST_INTERVAL:
            await message.reply("⏳ Подожди немного.")
            return
        user_last_request[user_id] = now

        video = message.video
        if video.file_size > 20 * 1024 * 1024:
            await message.reply("⚠️ Видео слишком большое (максимум 20 МБ).")
            return

        file = await bot.get_file(video.file_id)
        file_bytes = await bot.download_file(file.file_path)
        raw_bytes = file_bytes.getvalue()
        print(f"   📥 Видео скачано, размер {len(raw_bytes)} байт")

        cache_key = get_cache_key(raw_bytes)
        cached = await get_cached_result(cache_key)
        if cached:
            print("   ♻️ Используем кеш")
            user_data[user_id] = {
                'results': cached['result'],
                'index': 0,
                'video_bytes': raw_bytes,
                'search_count': 0
            }
            await show_result(message, user_id)
            return

        percentages_first = [5, 20, 50, 70, 95]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(raw_bytes)
            video_path = tmp_file.name

        frames = extract_frames(video_path, percentages_first)
        os.unlink(video_path)

        if not frames:
            await message.reply("⚠️ Не удалось извлечь кадры из видео.")
            return

        found_result = None
        for idx, frame_bytes in enumerate(frames):
            print(f"   🔍 Ищем по кадру {idx+1}/{len(frames)}")
            compressed = compress_image(frame_bytes)
            result = await search_by_frame(compressed)
            if result.get('result') and len(result['result']) > 0:
                found_result = result
                break
            await asyncio.sleep(0.5)

        if not found_result:
            await message.reply("😔 По этому видео ничего не найдено.")
            return

        results_list = found_result['result']
        if not results_list or not isinstance(results_list[0], dict):
            await message.reply("⚠️ Неверный формат данных от сервиса.")
            return

        cache_result(cache_key, {'result': results_list})
        user_data[user_id] = {
            'results': results_list,
            'index': 0,
            'video_bytes': raw_bytes,
            'search_count': 0
        }
        await show_result(message, user_id)

    except Exception as e:
        print(f"❌ Ошибка handle_video:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой файл или /start")

# --- Функция показа результата ---
async def show_result(message: Message, user_id: int):
    print(f"📤 Показ результата для {user_id}")
    try:
        data = user_data.get(user_id)
        if not data:
            await message.reply("⚠️ Данные не найдены. Попробуй /start")
            return

        results = data.get('results')
        idx = data.get('index', 0)

        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            print(f"❌ Некорректные данные: {type(results)}")
            await message.reply("⚠️ Ошибка данных. Попробуй /start заново.")
            user_data.pop(user_id, None)
            return

        if idx >= len(results):
            await message.reply("🏁 Это был последний результат. Попробуй другой файл.")
            user_data.pop(user_id, None)
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
        # Обновляем индекс в хранилище
        user_data[user_id]['index'] = idx

    except Exception as e:
        print(f"❌ Ошибка show_result: {e}")
        await message.reply("⚠️ Ошибка отображения результата. Попробуй /start.")

# --- Кнопка "Нет, ищи другое" ---
@dp.callback_query(lambda c: c.data == "next")
async def process_next(callback: CallbackQuery):
    user_id = callback.from_user.id
    print(f"🔄 Нажата 'Нет, ищи другое' от {user_id}")
    try:
        await callback.answer()
        data = user_data.get(user_id)
        if not data:
            await callback.message.edit_text("⚠️ Данные не найдены. Попробуй /start")
            return

        results = data.get('results')
        idx = data.get('index', 0)
        video_bytes = data.get('video_bytes')
        search_count = data.get('search_count', 0)

        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            await callback.message.edit_text("⚠️ Ошибка данных. Попробуй /start")
            user_data.pop(user_id, None)
            return

        # Если это видео и второй набор кадров ещё не использовался
        if video_bytes is not None and search_count == 0:
            print("   🔄 Используем второй набор кадров")
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
                user_data.pop(user_id, None)
                return

            new_results = found_result['result']
            if not new_results or not isinstance(new_results[0], dict):
                await callback.message.edit_text("⚠️ Неверный формат данных.")
                return

            # Обновляем данные пользователя
            user_data[user_id] = {
                'results': new_results,
                'index': 0,
                'video_bytes': video_bytes,
                'search_count': 1
            }
            await show_result(callback.message, user_id)
            try:
                await callback.message.delete()
            except Exception:
                pass
            return

        # Просто листаем результаты
        next_idx = idx + 1
        if next_idx >= len(results):
            await callback.message.edit_text("🏁 Это был последний результат.")
            user_data.pop(user_id, None)
            return

        user_data[user_id]['index'] = next_idx
        await show_result(callback.message, user_id)
        try:
            await callback.message.delete()
        except Exception:
            pass

    except Exception as e:
        print(f"❌ Ошибка process_next: {e}")
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
    print("🚀 Запуск main()")
    await set_default_commands()
    print("✅ Команды установлены")
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            start_web_server()
        )
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        await asyncio.sleep(5)
        os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == '__main__':
    print("🏁 Запуск скрипта")
    asyncio.run(main())
