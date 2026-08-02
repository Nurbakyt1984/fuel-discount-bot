import os
import re
import io
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from PIL import Image
import pytesseract

TOKEN = os.environ.get("BOT_TOKEN")

def extract_numbers(text):
    nums = re.findall(r"\d+\.\d+", text.replace(',', '.'))
    return [float(n) for n in nums]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот для расчета скидки готов!\n\n"
        "Отправь фото колонки TA (где 361.18 и 69.740)\n"
        "А в подписи к фото напиши цену из приложения, например: 4.80\n\n"
        "Я посчитаю скидку!"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Читаю фото...")
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()
        img = Image.open(io.BytesIO(img_bytes))
        
        text = pytesseract.image_to_string(img, config='--psm 6')
        nums = extract_numbers(text)
        
        if not nums:
            await update.message.reply_text(f"Не вижу цифр, попробуй четче сфоткать.\nЯ вижу текст: {text[:200]}")
            return

        total_sale = max([n for n in nums if n > 20], default=0)
        gallons = 0
        # gallons обычно 69.740 - ищем
        for n in nums:
            if 5 < n < 200 and n != total_sale:
                gallons = n
                break
        
        price_pump = min([n for n in nums if n < 10], default=0)
        
        caption_nums = extract_numbers(update.message.caption or "")
        price_app = min([n for n in caption_nums if n < 10], default=0) if caption_nums else 0

        msg = f"🔍 Нашел на фото: {nums}\n\n"
        if total_sale: msg += f"💰 This Sale: ${total_sale}\n"
        if gallons: msg += f"⛽ Gallons: {gallons}\n"
        if price_pump: msg += f"🏷️ Цена на колонке: ${price_pump}\n"
        if price_app: msg += f"📱 Цена в приложении: ${price_app}\n"

        if gallons and price_app and price_pump:
            disc_per = price_pump - price_app
            total_disc = disc_per * gallons
            msg += f"\n💸 РЕЗУЛЬТАТ:\nСкидка: ${disc_per:.3f} / галлон\nЭкономия: ${total_disc:.2f}\nИтого: ${total_sale} (экономия уже внутри)"
        elif gallons and total_sale:
            real_price = total_sale / gallons
            msg += f"\nФактическая цена: ${real_price:.3f} / галлон"

        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

if __name__ == "__main__":
    if not TOKEN:
        print("Нет BOT_TOKEN!")
        exit(1)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("Бот запущен...")
    app.run_polling()
