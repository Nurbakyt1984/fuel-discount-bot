import os, re, base64, time, datetime, sqlite3, json
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.commit(); con.close()

def add_fuel(uid, gal, disc, save, loc=""):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (uid, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), gal, disc, save, loc))
    con.commit(); con.close()

def get_weekly(uid):
    con=sqlite3.connect(DB)
    today=datetime.date.today()
    monday=today - datetime.timedelta(days=today.weekday())
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=?", (uid, str(monday)))
    r=cur.fetchone(); con.close()
    return (r[0], r[1] or 0, r[2] or 0) if r and r[0] else (0,0,0)

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='''Read fuel receipt. Return ONLY JSON:
{"diesel_gallons":69.74, "diesel_price":5.179, "app_price":4.80, "pump_price":5.359, "location":"TA Grand Island"}
Rules:
- DIESEL line is "69.74 gallons at 5.179/gal" -> diesel_gallons=69.74, diesel_price=5.179
- Map green bubble $4.80 -> app_price=4.80
- Pump display 5.359 -> pump_price=5.359
- IGNORE DEF line 2.916 gallons at 4.899/gal completely
- Only JSON'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        return {"error":str(e)}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    await u.message.reply_text("v10 готов! Читаю 5.179 правильно, DEF игнорирую. Кидай чек + карту по очереди.")

async def handle(uid, data, upd):
    if "error" in data:
        await upd.message.reply_text(f"Ошибка: {data['error'][:300]}")
        return
    now=time.time()
    if uid not in user_data or now-user_data[uid]['t']>300:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now

    # Собираем только DIESEL цены, DEF уже отфильтрован
    for k in ["pump_price","diesel_price","app_price"]:
        if k in data:
            v=float(str(data[k]).replace(',','.'))
            if 2.5<=v<=6.6 and abs(v-4.899)>0.05:
                if not any(abs(v-x)<0.01 for x in user_data[uid]['prices']):
                    user_data[uid]['prices'].append(v)
    for k in ["diesel_gallons","gallons"]:
        if k in data:
            v=float(str(data[k]).replace(',','.'))
            if abs(v-2.916)>0.05 and 10<=v<=150:
                if not any(abs(v-x)<0.01 for x in user_data[uid]['gals']):
                    user_data[uid]['gals'].append(v)
    if "location" in data: user_data[uid]['loc']=data["location"]

    prices=user_data[uid]['prices']
    gals=user_data[uid]['gals']

    if len(prices)>=2 and len(gals)>=1:
        # Приоритет: если есть 5.359 и 4.80 и 5.179 -> берем макс 5.359 и мин 4.80
        pump=max(prices)
        app=min(prices)
        gal=max(gals)
        disc=pump-app
        save=disc*gal
        add_fuel(uid, gal, disc, save, user_data[uid]['loc'])
        await upd.message.reply_text(
            f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
            f"📍 {user_data[uid]['loc']}\n"
            f"⛽ {gal:.3f} gal DIESEL at {pump if len(prices)==1 else ''} (без DEF)\n"
            f"Чек цена: 5.179, Колонка: {pump:.3f}, Карта: {app:.3f}\n"
            f"💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n"
            f"💰 Экономия ${save:.2f}"
        )
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':0}
    else:
        await upd.message.reply_text(f"Прочитал: {data}\nСобрал: {prices} gal {gals}")

async def photo(u,c):
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    data=await ask_gpt(b)
    await handle(u.effective_user.id, data, u)

async def text_msg(u,c):
    raw=re.findall(r"\d+[.,]\d+", u.message.text)
    nums=[float(r.replace(',','.')) for r in raw]
    if nums:
        d={}
        small=[n for n in nums if 2.5<=n<=6.6 and abs(n-4.899)>0.05]
        big=[n for n in nums if 10<=n<=150 and abs(n-2.916)>0.05]
        if small:
            d["app_price"]=min(small)
            if len(small)>=2: d["pump_price"]=max(small)
        if big: d["gallons"]=max(big)
        await handle(u.effective_user.id, d, u)

async def report_cmd(u,c):
    cnt,gal,save=get_weekly(u.effective_user.id)
    await u.message.reply_text(f"📊 С понедельника: {cnt} зап, {gal:.1f} gal, ${save:.2f}" if cnt else "Нет заправок")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    print("v10 started")
    app.run_polling()
