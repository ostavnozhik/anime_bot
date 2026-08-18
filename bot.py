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

ADMIN_ID = int(os.getenv('ADMIN_ID', '1528277045'))  # замените на свой ID
print(f"✅ ID администратора: {ADMIN_ID}")

try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    print("✅ FFmpeg доступен")
except Exception:
    print("❌ FFmpeg НЕ ДОСТУПЕН")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_data = {}          # {user_id: {...}}
banned_users = set()    # {user_id}
search_cache = {}
CACHE_TTL = 3600
total_requests = 0
search_history = defaultdict(list)  # {user_id: [{'time':..., 'type':..., 'result':...}]}
MAX_HISTORY = 20

def add_history(user_id: int, file_type: str, result: str = None):
    entry = {
        'time': time.time(),
        'type': file_type,
        'result': result or 'Ничего не найдено'
    }
    history = search_history[user_id]
    history.append(entry)
    if len(history) > MAX_HISTORY:
        history.pop(0)

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

user_last_request = defaultdict(float)
REQUEST_INTERVAL = 3

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

def safe_get_title(best: dict) -> str:
    try:
        if 'title' in best and isinstance(best['title'], dict):
            title = best['title']
            return title.get('romaji') or title.get('english') or title.get('native') or best.get('filename', 'Неизвестно')
        anilist = best.get('anilist')
        if isinstance(anilist, dict):
            title = anilist.get('title', {})
            if isinstance(title, dict):
                return title.get('romaji') or title.get('english') or title.get('native') or best.get('filename', 'Неизвестно')
        return best.get('filename', 'Неизвестно')
    except Exception:
        return "Неизвестно"

def safe_get_anilist_id(best: dict):
    try:
        anilist = best.get('anilist')
        if isinstance(anilist, dict):
            return anilist.get('id')
        elif isinstance(anilist, int):
            return anilist
        else:
            return None
    except Exception:
        return None

def compress_image(image_bytes: bytes, max_size: int = 800) -> bytes:
    print(f"   🖼️ Размер фото: {len(image_bytes)} байт")
    return image_bytes

def extract_frames_advanced(video_path: str, num_frames: int = 8) -> list:
    print(f"   🎬 Извлечение {num_frames} кадров...")
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
    step = 100 / (num_frames + 1)
    for i in range(1, num_frames + 1):
        pct = i * step
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
                print(f"   ✅ Кадр {i}/{num_frames} ({pct:.1f}%) извлечён, {len(stdout)} байт")
            else:
                print(f"   ⚠️ Ошибка кадра {i}: {stderr.decode()[:100]}")
        except Exception as e:
            print(f"   ❌ ffmpeg ошибка: {e}")
    return frames

# --- API functions ---
async def search_by_frame(image_bytes: bytes) -> dict:
    global total_requests
    total_requests += 1
    print(f"   🔍 Запрос к trace.moe, размер {len(image_bytes)} байт")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        data = aiohttp.FormData()
        data.add_field('image', image_bytes, filename='frame.jpg')
        async with session.post('https://api.trace.moe/search', data=data) as resp:
            result = await resp.json()
            print(f"   📨 Ответ trace.moe: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
            return result

async def search_saucenao(image_bytes: bytes) -> dict:
    print("   🔍 Запрос к SauceNAO...")
    api_key = os.getenv('SAUCENAO_API_KEY', '')
    url = "https://saucenao.com/search.php"
    params = {"output_type": 2, "api_key": api_key, "numres": 1, "db": 999}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        data = aiohttp.FormData()
        data.add_field('file', image_bytes, filename='image.jpg')
        async with session.post(url, params=params, data=data) as resp:
            result = await resp.json()
            print(f"   📨 Ответ SauceNAO: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
            return result

def parse_saucenao_result(data: dict) -> dict:
    try:
        results = data.get('results', [])
        if not results:
            return None
        first = results[0]
        if first.get('header', {}).get('similarity', 0) < 60:
            return None
        data_obj = first.get('data', {})
        title = data_obj.get('title') or data_obj.get('source') or data_obj.get('eng_name')
        if not title:
            return None
        return {
            'title': title,
            'similarity': first['header']['similarity'],
            'url': data_obj.get('ext_urls', [''])[0] if data_obj.get('ext_urls') else None
        }
    except Exception:
        return None

# --- Админ-функции ---
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def get_user_mention(user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        name = user.first_name or user.username or str(user_id)
        return f"[{name}](tg://user?id={user_id})"
    except:
        return str(user_id)

# --- Команды ---
async def set_default_commands():
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="history", description="📋 Моя история запросов"),
    ]
    await bot.set_my_commands(commands)
    print("✅ Меню команд установлено")

@dp.message(Command('start'))
async def start_command(message: Message):
    user_id = message.from_user.id
    if user_id in banned_users:
        await message.reply("⛔ Вы забанены. Обратитесь к администратору.")
        return
    user_data.pop(user_id, None)
    print(f"📩 /start от {user_id}")
    await message.reply(
        "👋 Привет! Отправь скриншот или видео, и я найду аниме.\n\n❓ Помощь — /help",
        reply_markup=help_kb
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    user_id = message.from_user.id
    if user_id in banned_users:
        await message.reply("⛔ Вы забанены.")
        return
    print(f"📩 /help от {user_id}")
    await message.reply(
        "🤖 **Бот для поиска аниме по кадру**\n\n"
        "📸 Отправьте скриншот или видео — я найду тайтл.\n"
        "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
        "🔗 В ответе даю ссылку на AniList.\n\n"
        "📋 Команда /history — показать ваши последние запросы."
    )

@dp.message(Command('history'))
async def history_command(message: Message):
    user_id = message.from_user.id
    history = search_history.get(user_id, [])
    if not history:
        await message.reply("📭 У вас пока нет запросов.")
        return
    lines = []
    for i, entry in enumerate(history[-10:], 1):
        dt = time.strftime('%d.%m %H:%M', time.localtime(entry['time']))
        result = entry['result'][:30] + '…' if len(entry['result']) > 30 else entry['result']
        lines.append(f"{i}. {dt} [{entry['type']}] {result}")
    await message.reply("📋 **Ваша история запросов (последние 10):**\n\n" + "\n".join(lines))

# --- Админ-команды ---
@dp.message(Command('admin'))
async def admin_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.reply(
        "👑 **Админ-панель**\n\n"
        "/stats — статистика\n"
        "/clear_cache — очистить кеш\n"
        "/broadcast <текст> — рассылка\n"
        "/ban <@username или ID> — заблокировать\n"
        "/unban <@username или ID> — разблокировать\n"
        "/blocked — список забаненных\n"
        "/user_history <ID> — история пользователя\n"
        "/admin — эта справка"
    )

@dp.message(Command('stats'))
async def stats_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    total_users = len(user_data)
    cached = len(search_cache)
    banned = len(banned_users)
    await message.reply(
        f"📊 **Статистика**\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"🗄 Кеш: {cached} записей\n"
        f"🚫 Забанено: {banned}\n"
        f"📨 Всего запросов к API: {total_requests}"
    )

@dp.message(Command('clear_cache'))
async def clear_cache_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    size_before = len(search_cache)
    search_cache.clear()
    await message.reply(f"🧹 Кеш очищен. Удалено {size_before} записей.")

@dp.message(Command('broadcast'))
async def broadcast_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        await message.reply("⚠️ Напишите текст рассылки: /broadcast <текст>")
        return
    users = list(user_data.keys())
    if not users:
        await message.reply("❌ Нет активных пользователей для рассылки.")
        return
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 **Рассылка от администратора**\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.reply(f"✅ Рассылка отправлена {sent} пользователям из {len(users)}.")

@dp.message(Command('ban'))
async def ban_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Укажите пользователя: /ban @username или /ban 123456789")
        return
    target = args[1].strip()
    user_id = None
    if target.startswith('@'):
        try:
            chat = await bot.get_chat(target)
            user_id = chat.id
        except:
            await message.reply("❌ Не удалось найти пользователя с таким username.")
            return
    elif target.isdigit():
        user_id = int(target)
    else:
        await message.reply("❌ Неверный формат. Используйте @username или числовой ID.")
        return
    if user_id == ADMIN_ID:
        await message.reply("❌ Нельзя забанить самого себя.")
        return
    banned_users.add(user_id)
    mention = await get_user_mention(user_id)
    await message.reply(f"✅ Пользователь {mention} забанен.")

@dp.message(Command('unban'))
async def unban_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Укажите пользователя: /unban @username или /unban 123456789")
        return
    target = args[1].strip()
    user_id = None
    if target.startswith('@'):
        try:
            chat = await bot.get_chat(target)
            user_id = chat.id
        except:
            await message.reply("❌ Не удалось найти пользователя с таким username.")
            return
    elif target.isdigit():
        user_id = int(target)
    else:
        await message.reply("❌ Неверный формат.")
        return
    if user_id not in banned_users:
        await message.reply("⚠️ Этот пользователь не забанен.")
        return
    banned_users.remove(user_id)
    mention = await get_user_mention(user_id)
    await message.reply(f"✅ Пользователь {mention} разбанен.")

@dp.message(Command('blocked'))
async def blocked_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    if not banned_users:
        await message.reply("🚫 Нет забаненных пользователей.")
        return
    lines = []
    for uid in list(banned_users):
        mention = await get_user_mention(uid)
        lines.append(f"• {mention}")
    await message.reply("🚫 **Забаненные пользователи:**\n\n" + "\n".join(lines))

@dp.message(Command('user_history'))
async def user_history_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Укажите ID пользователя: /user_history 123456789")
        return
    try:
        target_id = int(args[1])
    except:
        await message.reply("❌ ID должен быть числом.")
        return
    history = search_history.get(target_id, [])
    if not history:
        await message.reply(f"📭 У пользователя {target_id} нет запросов.")
        return
    lines = []
    for i, entry in enumerate(history[-20:], 1):
        dt = time.strftime('%d.%m %H:%M', time.localtime(entry['time']))
        result = entry['result'][:30] + '…' if len(entry['result']) > 30 else entry['result']
        lines.append(f"{i}. {dt} [{entry['type']}] {result}")
    await message.reply(f"📋 **История пользователя {target_id} (последние 20):**\n\n" + "\n".join(lines))

# --- Основные хендлеры ---
@dp.callback_query(lambda c: c.data == "help")
async def process_help(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in banned_users:
        await callback.answer("⛔ Вы забанены.")
        return
    print(f"📩 Кнопка помощи от {user_id}")
    try:
        await callback.answer()
        await callback.message.answer(
            "🤖 **Бот для поиска аниме по кадру**\n\n"
            "📸 Отправьте скриншот или видео — я найду тайтл.\n"
            "🔄 Если результат не тот — нажмите «Нет, ищи другое».\n"
            "🔗 В ответе даю ссылку на AniList."
        )
    except Exception as e:
        print(f"❌ Ошибка process_help: {e}")

@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    if user_id in banned_users:
        await message.reply("⛔ Вы забанены.")
        return
    print(f"📸 Обработка фото от {user_id}")
    user_data.pop(user_id, None)
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
            # Запись в историю (берём название из кеша)
            title = safe_get_title(cached['result'][0]) if cached['result'] else None
            add_history(user_id, 'фото', title)
            await show_result(message, user_id)
            return

        compressed = compress_image(raw_bytes)
        result = await search_by_frame(compressed)

        if result.get('error'):
            await message.reply(f"⚠️ Ошибка API: {result['error']}")
            return

        if not isinstance(result.get('result'), list) or len(result['result']) == 0:
            add_history(user_id, 'фото', None)
            await message.reply("😔 Ничего не найдено. Попробуй другой скриншот.")
            return

        results_list = result['result']
        if not results_list or not isinstance(results_list[0], dict):
            await message.reply("⚠️ Неверный формат данных от сервиса.")
            return

        title = safe_get_title(results_list[0])
        cache_result(cache_key, {'result': results_list})
        user_data[user_id] = {
            'results': results_list,
            'index': 0,
            'video_bytes': None,
            'search_count': 0
        }
        add_history(user_id, 'фото', title)
        await show_result(message, user_id)

    except asyncio.TimeoutError:
        await message.reply("⏳ Сервис поиска долго отвечает.")
    except Exception as e:
        print(f"❌ Ошибка handle_photo:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой скриншот или /start")

@dp.message(lambda msg: msg.video is not None)
async def handle_video(message: Message):
    user_id = message.from_user.id
    if user_id in banned_users:
        await message.reply("⛔ Вы забанены.")
        return
    print(f"🎬 Обработка видео от {user_id}")
    user_data.pop(user_id, None)
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
            title = safe_get_title(cached['result'][0]) if cached['result'] else None
            add_history(user_id, 'видео', title)
            await show_result(message, user_id)
            return

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(raw_bytes)
            video_path = tmp_file.name

        frames = extract_frames_advanced(video_path, num_frames=8)
        os.unlink(video_path)

        if not frames:
            await message.reply("⚠️ Не удалось извлечь кадры из видео.")
            return

        found_result = None
        for idx, frame_bytes in enumerate(frames[:3]):
            print(f"   🔍 trace.moe кадр {idx+1}/3")
            compressed = compress_image(frame_bytes)
            result = await search_by_frame(compressed)
            if result.get('result') and len(result['result']) > 0:
                if result['result'][0]['similarity'] > 0.6:
                    found_result = result
                    break
            await asyncio.sleep(0.2)

        if not found_result:
            print("   🔄 trace.moe не нашёл, пробуем SauceNAO (1 кадр)...")
            mid_frame = frames[len(frames)//2]
            try:
                saucenao_result = await asyncio.wait_for(
                    search_saucenao(mid_frame),
                    timeout=5.0
                )
                parsed = parse_saucenao_result(saucenao_result)
                if parsed:
                    title = parsed['title']
                    similarity = parsed['similarity']
                    url = parsed.get('url')
                    answer = (
                        f"✅ Найдено через SauceNAO!\n"
                        f"📺 Название: {title}\n"
                        f"🎯 Точность: {similarity:.2f}%\n"
                    )
                    if url:
                        answer += f"\n🔗 [Ссылка]({url})"
                    await message.reply(answer)
                    user_data[user_id] = {
                        'results': [{'title': title, 'similarity': similarity/100, 'url': url, 'from': 0, 'episode': 'неизвестно'}],
                        'index': 0,
                        'video_bytes': raw_bytes,
                        'search_count': 0
                    }
                    add_history(user_id, 'видео', title)
                    return
                else:
                    add_history(user_id, 'видео', None)
                    await message.reply("😔 Ни trace.moe, ни SauceNAO не нашли аниме.")
                    return
            except asyncio.TimeoutError:
                await message.reply("⏳ SauceNAO долго отвечает, попробуй позже.")
                return

        results_list = found_result['result']
        if not results_list or not isinstance(results_list[0], dict):
            await message.reply("⚠️ Неверный формат данных от сервиса.")
            return

        title = safe_get_title(results_list[0])
        cache_result(cache_key, {'result': results_list})
        user_data[user_id] = {
            'results': results_list,
            'index': 0,
            'video_bytes': raw_bytes,
            'search_count': 0
        }
        add_history(user_id, 'видео', title)
        await show_result(message, user_id)

    except Exception as e:
        print(f"❌ Ошибка handle_video:\n{traceback.format_exc()}")
        await message.reply("⚠️ Ошибка, попробуй другой файл или /start")

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
            user_data.pop(user_id, None)
            await message.reply("⚠️ Ошибка данных. Попробуй /start заново.")
            return

        if idx >= len(results):
            await message.reply("🏁 Это был последний результат. Попробуй другой файл.")
            user_data.pop(user_id, None)
            return

        best = results[idx]

        if 'anilist' not in best:
            name = best.get('title', 'Неизвестно')
            similarity = best.get('similarity', 0.0) * 100
            url = best.get('url')
            answer = (
                f"✅ Найдено (SauceNAO)!\n"
                f"📺 Название: {name}\n"
                f"🎯 Точность: {similarity:.2f}%\n"
            )
            if url:
                answer += f"\n🔗 [Ссылка]({url})"
            await message.reply(answer, reply_markup=next_kb)
            user_data[user_id]['index'] = idx
            return

        name = safe_get_title(best)
        episode = best.get('episode', 'неизвестно')
        from_time = best.get('from', 0.0)
        similarity = best.get('similarity', 0.0) * 100
        time_str = format_time(from_time)

        anilist_id = safe_get_anilist_id(best)
        anilist_url = f"https://anilist.co/anime/{anilist_id}" if anilist_id else None

        answer = (
            f"✅ Найдено!\n"
            f"📺 Название: {name}\n"
            f"🎬 Эпизод: {episode}\n"
            f"⏱ Время: {time_str}\n"
            f"🎯 Точность: {similarity:.2f}%\n"
            f"({idx+1}/{len(results)})"
        )
        if anilist_url:
            answer += f"\n\n🔗 [Смотреть на AniList]({anilist_url})"

        await message.reply(answer, reply_markup=next_kb)
        user_data[user_id]['index'] = idx

    except Exception as e:
        print(f"❌ Ошибка show_result: {e}")
        await message.reply("⚠️ Ошибка отображения результата. Попробуй /start.")

@dp.callback_query(lambda c: c.data == "next")
async def process_next(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in banned_users:
        await callback.answer("⛔ Вы забанены.")
        return
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

        if video_bytes is not None and search_count == 0:
            print("   🔄 Используем второй набор кадров (8 кадров со смещением)")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_file.write(video_bytes)
                video_path = tmp_file.name

            frames = extract_frames_advanced(video_path, num_frames=8)
            os.unlink(video_path)

            if not frames:
                await callback.message.edit_text("⚠️ Не удалось извлечь кадры.")
                return

            found_result = None
            for frame_bytes in frames:
                compressed = compress_image(frame_bytes)
                result = await search_by_frame(compressed)
                if result.get('result') and len(result['result']) > 0:
                    if result['result'][0]['similarity'] > 0.6:
                        found_result = result
                        break
                await asyncio.sleep(0.2)

            if not found_result:
                await callback.message.edit_text("😔 Второй набор кадров ничего не дал.")
                user_data.pop(user_id, None)
                return

            new_results = found_result['result']
            if not new_results or not isinstance(new_results[0], dict):
                await callback.message.edit_text("⚠️ Неверный формат данных.")
                return

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
