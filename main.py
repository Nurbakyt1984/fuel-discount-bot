import os
import re
import io
import time
from PIL import Image, ImageOps, ImageEnhance
import pytesseract
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")

# Хранилище для фото из альбома (3 фото за 1 раз)
user_photos = {}

def preprocess_for_ocr(pil_img):
    # Увеличиваем и чистим для чтения цифр на колонке
    img = pil_img.convert("L")
    w, h = img.size
    img = img.resize((w*3, h*3), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=2)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)
    # Черно-белый порог
    img = img.point(lambda x: 255 if x > 160 else 0)
    return img

def extract_numbers_from_text(text):
    text = text.replace(',', '.').replace(' ', '')
    # Ищем цифры типа 5.359, 361.18, 69.740, 4.80, 0.2
    nums = re.findall(r"\d+\.\d+", text)
    return [float(n) for n in nums]

async def ocr_photo(pil_img):
    all_nums = []
    # Пробуем 3 варианта обработки
    for mode in [0, 1, 2]:
        if mode == 0:
            processed = preprocess_for_ocr(pil_img)
        elif mode == 1:
            processed = ImageOps.invert(preprocess_for_ocr(pil_img))
        else:
            processed = pil_img.convert("L").resize((pil_img.size[0]*4, pil_img.size[1]*4), Image.LANCZOS)

        for psm in ['6', '7', '11']:
            try:
                txt = pytesseract.image_to_string(processed, config=f'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.$')
                all_nums.extend(extract_numbers_from_text(txt))
            except:
                pass
    return list(set(all_nums))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Версия 3.0 - ЧИТАЮ ФОТО АВТОМАТОМ!\n\n"
        "Просто скинь 2-3 фото как раньше:\n"
        "1. Скрин карты с $4.80\n"
        "2. Фото колонки с 5.359\n"
        "3. Фото с 361.18 и 69.740\n\n"
        "Можешь кинуть все 3 сразу - я сложу и посчитаю скидку!"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("📸 Читаю...")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()
        pil_img = Image.open(io.BytesIO(img_bytes))

        nums = await ocr_photo(pil_img)
        print(f"OCR found: {nums}")

        # Сохраняем цифры пользователя за последние 60 сек
        now = time.time()
        if user_id not in user_photos or now - user_photos[user_id]['time'] > 60:
            user_photos[user_id] = {'nums': [], 'time': now}

        user_photos[user_id]['nums'].extend(nums)
        user_photos[user_id]['time'] = now

        combined = user_photos[user_id]['nums']

        # Ищем нужные цифры
        price_app = 0
        price_pump = 0
        total_sale = 0
        gallons = 0

        # $4.80 обычно на карте, 3-6 диапазон
        small_prices = [n for n in combined if 3.0 <= n <= 6.5]
        if small_prices:
            # Самая маленькая это $4.80, самая большая 5.359
            price_app = min(small_prices)
            price_pump = max(small_prices)

        large = [n for n in combined if n > 20]
        if large:
            total_sale = max(large)

        # Галлоны 69.740 - от 5 до 200 но не цена
        mid = [n for n in combined if 5 < n < 150 and n not in small_prices and n!= total_sale]
        if mid:
            gallons = max(mid)

        # Если уже есть 2 главных цифры - считаем
        if gallons and price_app:
            if not price_pump:
                price_pump = 5.359

            disc_per = price_pump - price_app
            total_disc = disc_per * gallons
            final_price = gallons * price_app

            msg = f"✅ НАШЕЛ ВСЕ!\n"
            msg += f"💰 На колонке: ${total_sale if total_sale else '?'}\n"
            msg += f"⛽ Галлоны: {gallons}\n"
            msg += f"🏷️ Цена колонки: ${price_pump}\n"
            msg += f"📱 Цена приложения: ${price_app}\n\n"
            msg += f"💸 СКИДКА: ${disc_per:.3f} / галлон\n"
            msg += f"💵 Экономия: ${total_disc:.2f}\n"
            msg += f"💳 К оплате: ${final_price:.2f}"

            await update.message.reply_text(msg)
            user_photos[user_id] = {'nums': [], 'time': 0} # сброс
        else:
            await update.message.reply_text(f"Пока вижу: {combined}\nКинь еще фото колонки...")

    except Exception as e:
        await update.message.reply_text(f"Ошибка чтения: {e}")
        print(e)

if __name__ == "__main__":
    if not TOKEN:
        print("Нет BOT_TOKEN")
        exit(1)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Bot v3.0 started")
    app.run_polling()
