import os, re, base64, time, datetime, sqlite3, json
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.commit(); con.close()

def add_fuel(uid, gal, disc, save, loc=""):
    con=sqlite3.connect(DB)
    dt=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (uid, dt, gal, disc, save, loc))
    con.commit(); con.close()

def get_weekly(uid):
    con=sqlite3.connect(DB)
    today=datetime.date.today()
    monday=today - datetime.timedelta(days=today.weekday())
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=?", (uid, str(monday)))
    r=cur.fetchone(); con.close()
    if not r or r[0] is None: return (0,0,0)
    return (r[0], r[1] or 0, r[2] or 0)

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='''Read fuel image. Return ONLY JSON.
Pump: {"pump_price":5.359, "gallons":69.74, "sale":361.18}
Map: {"app_price":4.80}
Receipt TA: {"diesel_gallons":69.74, "diesel_price":5.179, "location":"TA Grand Island"}
If 36 1.18 -> 361.18. No text, only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],
            max_tokens=120)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print("GPT:", txt)
        return json.loads(txt)
    except Exception as e:
        print("GPT err", e)
        return {}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    await u.message.reply_text("✅ v7 готов! Кидай фото колонки, карты или чека TA. Понимаю всё по-разному.")

async def handle(uid, data, upd):
    now=time.time()
    if uid not in user_data or now-user_data[uid]['t']>300:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now
    if "pump_price" in data: user_data[uid]['prices'].append(float(data["pump_price"]))
    if "app_price" in data: user_data[uid]['prices'].append(float(data["app_price"]))
    if "diesel_price" in data: user_data[uid]['prices'].append(float(data["diesel_price"]))
    if "gallons" in data: user_data[uid]['gals'].append(float(data["gallons"]))
    if "diesel_gallons" in data: user_data[uid]['gals'].append(float(data["diesel_gallons"]))
    if "location" in data: user_data[uid]['loc']=data["location"]
    print("STATE", user_data[uid])

    prices=user_data[uid]['prices']
    gals=user_data[uid]['gals']

    if len(prices)>=2 and len(gals)>=1:
        # скидка = макс цена - мин цена
        pump=max(prices)
        app=min(prices)
        gal=max(gals)
        disc=pump-app
        if disc<0: disc=-disc
        if disc>2: # защита от глюка
            disc=sorted(prices)[-1]-sorted(prices)[-2]
        save=disc*gal
        loc=user_data[uid]['loc']
        add_fuel(uid, gal, disc, save, loc)
        await upd.message.reply_text(f"📅 Дата: {datetime.datetime.now().strftime('%m/%d/%Y')}\n📍 {loc}\n⛽ Галлонов: {gal:.3f}\n💸 Скидка: ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n💰 Экономия: ${save:.2f}")
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':0}
    elif len(prices)>=1 or len(gals)>=1:
        await upd.message.reply_text(f"Принял {data} ✅ Собрал цены {prices} галлоны {gals}. Кинь еще фото или напиши цену типа 4.80")

async def photo(u,c):
    uid=u.effective_user.id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    data=await ask_gpt(b)
    if not data:
        await u.message.reply_text("Не вижу цифр, кропни крупнее или напиши 4.80 текстом")
        return
    await handle(uid, data, u)

async def text_msg(u,c):
    t=u.message.text.replace('$','').replace(',','.')
    nums=[float(x) for x in re.findall(r"\d+\.\d+", t)]
    if not nums: return
    d={}
    if len(nums)==1: d={"app_price":nums[0]}
    else: d={"app_price":min(nums), "pump_price":max(nums)}
    await handle(u.effective_user.id, d, u)

async def report_cmd(u,c):
    cnt,gal,save=get_weekly(u.effective_user.id)
    if cnt==0: await u.message.reply_text("На этой неделе нет заправок")
    else: await u.message.reply_text(f"📊 Отчет с понедельника:\n⛽ Заправок: {cnt}\n🛢️ Галлонов: {gal:.1f}\n💰 Экономия: ${save:.2f}")

async def weekly_job(ctx):
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT user_id, chat_id FROM users")
    for uid,cid in cur.fetchall():
        cnt,gal,save=get_weekly(uid)
        if cnt>0:
            try: await ctx.bot.send_message(cid, text=f"📊 ОТЧЕТ ПЯТНИЦА 8AM CT:\n⛽ Заправок: {cnt}\n🛢️ Всего: {gal:.1f} gal\n💰 Скидка: ${save:.2f}")
            except: pass
    con.close()

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    app.job_queue.run_daily(weekly_job, time=datetime.time(hour=13, minute=0, second=0), days=(4,))
    print("v7 started")
    app.run_polling()
