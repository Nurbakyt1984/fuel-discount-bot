import os, re, io, base64, time
from PIL import Image
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

user_photos = {}

def extract_numbers(text):
    return [float(n) for n in re.findall(r"\d+\.\d+", text.replace(',','.'))]

async def ask_gpt(image_bytes):
    b64 = base64.b64encode(image_bytes).decode()
    prompt = """Read all numbers from this fuel station photo.
    Find: This Sale $ (like 361.18), Gallons (like 69.740), Price per gallon $ (like 5.359), and app price $ (like 4.80 if visible on map).
    Return ONLY numbers like: 361.18, 69.740, 5.359, 4.80
    If you see a map with $4.80, include it."""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role":"user",
                "content":[
                    {"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}
                ]
            }],
            max_tokens=100
        )
        return resp.choices[0].message.content
    except Exception as e:
        return str(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ v4.0 AI готов! Кидай 3 фото и я сам прочитаю все цифры!")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("🤖 AI читает фото... 3 сек")

    photo = update.message.photo[-1]
    file = await photo.get_file()
    img_bytes = await file.download_as_bytearray()

    text = await ask_gpt(bytes(img_bytes))
    nums = extract_numbers(text)

    now = time.time()
    if user_id not in user_photos or now - user_photos[user_id]['time'] > 90:
        user_photos[user_id] = {'nums': [], 'time': now}
    user_photos[user_id]['nums'].extend(nums)
    user_photos[user_id]['time'] = now
    combined = user_photos[user_id]['nums']

    # считаем
    small = [n for n in combined if 3 <= n <= 6.5]
    price_app = min(small) if small else 0
    price_pump = max(small) if len(small)>1 else (small[0] if small else 5.359)
    total_sale = max([n for n in combined if n>100], default=0)
    gallons = max([n for n in combined if 5<n<150 and n not in small and n!=total_sale], default=0)

    if gallons and price_app:
        disc = price_pump - price_app
        await update.message.reply_text(
            f"✅ Прочитал: {text}\n"
            f"Все цифры: {combined}\n\n"
            f"💰 Sale: ${total_sale}\n⛽ Gal: {gallons}\n🏷️ Pump: ${price_pump}\n📱 App: ${price_app}\n\n"
            f"💸 Скидка ${disc:.3f}/gal\n💵 Экономия ${disc*gallons:.2f}"
        )
        user_photos[user_id] = {'nums': [], 'time': 0}
    else:
        await update.message.reply_text(f"Пока вижу: {text} -> {nums}\nСобрал: {combined}\nКидай еще фото...")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling()
