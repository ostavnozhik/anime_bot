@dp.message(lambda msg: msg.photo is not None)
async def handle_photo(message: Message):
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        raw_bytes = file_bytes.getvalue()

        # Отправляем в trace.moe
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', raw_bytes, filename='screenshot.jpg')
            async with session.post('https://api.trace.moe/search', data=data) as resp:
                result = await resp.json()

        if result.get('result') and len(result['result']) > 0:
            best = result['result'][0]
            title = best['anilist']['title']['romaji']
            episode = best.get('episode', '?')
            time_str = f"{int(best['from']//60)} мин {int(best['from']%60)} сек"
            await message.reply(f"✅ Найдено!\n📺 {title}\n🎬 {episode}\n⏱ {time_str}")
        else:
            await message.reply("😔 Ничего не найдено.")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")
