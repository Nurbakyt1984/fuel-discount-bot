import os, re, io, base64, time
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
    prompt = "Read numbers. Return ONLY numbers like 361.18, 69.74, 5.359, 4.80"
    for i in range(3):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":[
                    {"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],
                max_tokens=100
            )
            return resp.choices[0].message.content
        except Exception as e:
            if "429" in str(e): time.sleep(1)
            else: return str(e)
    return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ v5.1 готов! Кидай 2 фото: карту ($4.80) и колонку (361.18 / 69.74)")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    img_bytes = bytes(await file.download_as_bytearray())
    text = await ask_gpt(img_bytes)
    nums = extract_numbers(text)

    now = time.time()
    if user_id not in user_photos or now - user_photos[user_id]['time']>90:
        user_photos[user_id]={'nums':[],'time':now}
    user_photos[user_id]['nums'].extend(nums)
    user_photos[user_id]['time']=now
    combined = user_photos[user_id]['nums']

    # Логика v5.1 - умная
    small = [n for n in combined if 3 <= n <= 6.6]
    large = [n for n in combined if n>100]
    mid = [n for n in combined if 5<n<200 and n not in small and n not in large]

    app_price = min(small) if small else 0
    # Pump price: если есть 2 маленькие цены, берем большую, если нет - считаем Sale/Gallons
    if len(small)>=2:
        pump_price = max(small)
    elif large and mid:
        pump_price = large[0]/mid[0] if mid[0]!=0 else 0
    else:
        pump_price = 0

    sale = max(large) if large else 0
    gal = max(mid) if mid else 0
    if not gal and len(mid)==0 and sale and pump_price:
        gal = sale/pump_price

    if gal and app_price and pump_price:
        disc = pump_price - app_price
        await update.message.reply_text(
            f"✅ Прочитал: {text}\n"
            f"Sale: ${sale:.2f} Gal: {gal:.3f}\n"
            f"Pump: ${pump_price:.3f} App: ${app_price:.2f}\n\n"
            f"💸 Скидка ${disc:.3f}/gal\n💵 Экономия ${disc*gal:.2f}\n💳 К оплате ${gal*app_price:.2f}"
        )
        user_photos[user_id]={'nums':[],'time':0}
    else:
        await update.message.reply_text(f"Пока: {text} -> {nums}\nСобрал: {combined}\nКидай еще 1 фото...")

if __name__=="__main__":
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.run_polling()
