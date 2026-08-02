import os, re, base64, time, datetime, sqlite3, json
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, location TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.commit(); con.close()

def add_fuel(user_id, gallons, disc, saving, loc=""):
    con=sqlite3.connect(DB)
    date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (user_id, date, gallons, disc, saving, loc))
    con.commit(); con.close()

def get_weekly(user_id):
    con=sqlite3.connect(DB)
    today=datetime.date.today()
    monday=today - datetime.timedelta(days=today.weekday())
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=?", (user_id, str(monday)))
    row=cur.fetchone()
    con.close()
    if not row or row[0] is None: return (0,0,0)
    return (row[0], row[1] or 0, row[2] or 0)

def nums(t):
    t = t.replace('$','').replace(',','.').lower()
    return [float(n) for n in re.findall(r"\d+\.\d+", t) if 0.5 < float(n) < 1000]

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt = """
You are fuel receipt OCR. Look at image and return JSON only.

If it's pump display: {"pump_price": 5.359, "sale": 361.18, "gallons": 69.74}
If it's map: {"app_price": 4.80}
If it's TA receipt like in example: {"diesel_gallons": 69.74, "diesel_price": 5.179, "def_gallons": 2.916, "def_price": 4.899, "total": 375.47, "location": "TA Grand Island"}

Return ONLY valid JSON. No text.
If 361.18 looks like 36 1.18, fix it to 361.18.
"""
    try:
        r=client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],
            max_tokens=150
        )
        txt = r.choices[0].message.content.strip()
        # Достаем JSON даже если GPT добавил текст
        m = re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt = m.group(0)
        print(f"GPT JSON: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT error {e}")
        return {}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gallons':[], 'sale':[], 't':0, 'loc':""}
    await u.message.reply_text("✅ v7 готов! Кидай любые фото: колонка, карта $4.80, или чек TA. Понимаю всё.")

async def handle_data(uid, data, update):
    now=time.time()
    if uid not in user_data or now - user_data[uid]['t'] > 180:
        user_data[uid]={'prices':[], 'gallons':[], 'sale':[], 't':now, 'loc':""}
    user_data[uid]['t']=now

    # Собираем данные
    if "pump_price" in data: user_data[uid]['prices'].append(data["pump_price"])
    if "app_price" in data: user_data[uid]['prices'].append(data["app_price"])
    if "diesel_price" in data: user_data[uid]['prices'].append(data["diesel_price"])
    if "gallons" in data: user_data[uid]['gallons'].append(data["gallons"])
    if "diesel_gallons" in data: user_data[uid]['gallons'].append(data["diesel_gallons"])
    if "sale" in data: user_data[uid]['sale'].append(data["sale"])
    if "location" in data: user_data[uid]['loc']=data["location"]
    if "total" in data and "diesel_gallons" in data: # чек
        user_data[uid]['sale'].append(data["total"])

    prices = user_data[uid]['prices']
    gallons = user_data[uid]['gallons']
    sales = user_data[uid]['sale']

    print(f"STATE prices={prices} gallons={gallons} sale={sales}")

    # Логика расчета
    if len(prices) >= 1 and len(gallons) >= 1:
        # Если есть 2 цены - считаем скидку
        if len(prices) >= 2:
            p_sorted = sorted(prices)
            # Игнорируем DEF цену 4.899 если она мешает - берем дизельные цены
            # Дизель обычно 5.1-5.3, DEF 4.8-4.9, карта 4.80
            # Скидка = макс - мин
            pump = max(prices)
            app = min(prices)
            # Если в чеке есть и дизель и DEF, app будет DEF - это не правильно, берем 2 самых больших для дизеля
            if len(prices)>=3:
                # Убираем самую маленькую если это DEF и есть еще маленькая (карта)
                pass

            gal = max(gallons)
            disc = pump - app
            if disc < 0: disc = -disc
            if disc > 1.5: # Если скидка больше $1.5 - это глюк, берем pump - 2-я цена
                disc = sorted(prices)[-1] - sorted(prices)[-2]

            saving = disc * gal
            date_now=datetime.datetime.now().strftime("%m/%d/%Y")
            loc = user_data[uid]['loc']

            add_fuel(uid, gal, disc, saving, loc)
            await update.message.reply_text(f"📅 Дата: {date_now}\n📍 {loc}\n⛽ Галлонов: {gal:.3f}\n💸 Скидка: ${disc:.3f}/gal ( {pump:.3f} - {app:.3f} )\n💰 Экономия: ${saving:.2f}")
            user_data[uid]={'prices':[], 'gallons':[], 'sale':[], 't':0, 'loc':""}
        else:
            # Только одна цена и галлоны - ждем вторую цену или считаем если это чек
            if len(sales)==0 and len(gallons)>=1:
                await update.message.reply_text(f"Принял: цена {prices[0]}, галлонов {gallons[0]} ✅ Теперь кинь вторую цену (карту $4.80 или колонку 5.359) или напиши цифрами")
            else:
                # Если это чек типа 69.74 at 5.179 - считаем как есть, без скидки от колонки
                gal = max(gallons)
                price = prices[0]
                # Если есть sale и gallons, вычислим pump из sale
                if sales:
                    # total $375 включает DEF, нельзя
                    pass
                await update.message.reply_text(f"Принял чек: {gal} gal по {price}. Кинь еще фото колонки 5.359 чтобы посчитать скидку, или скажи /report")

async def photo(u,c):
    uid=u.effective_user.id
    file=await u.message.photo[-1].get_file()
    img=bytes(await file.download_as_bytearray())
    data=await ask_gpt(img)
    if not data:
        await u.message.reply_text("Не понял фото, попробуй крупнее или напиши цифры текстом")
        return
    await handle_data(uid, data, u)

async def text_msg(u,c):
    # Позволяет написать 4.80 текстом
    found=nums(u.message.text)
    if not found: return
    uid=u.effective_user.id
    # Если написал 2 числа - это цены
    data={}
    if len(found)==1:
        data={"app_price": found[0]}
    elif len(found)>=2:
        data={"app_price": found[0], "pump_price": found[1], "gallons": max(found) if max(found)>10 else 0}
        if data["gallons"]==0: del data["gallons"]
    await handle_data(uid, data, u)

async def report_cmd(u,c):
    count, gallons, saving = get_weekly(u.effective_user.id)
    if count==0: await u.message.reply_text("
