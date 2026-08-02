import os, re, base64, time, datetime, sqlite3, json, traceback
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
print(f"KEY exists: {bool(OPENAI_KEY)} starts: {OPENAI_KEY[:10] if OPENAI_KEY else 'NO'}")
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
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
    if not client:
        return {"error": "NO_KEY"}
    b64=base64.b64encode(b).decode()
    prompt='''You are fuel OCR. Image may contain 1-3 collaged photos (pump, map $4.80, receipt 69.74 at 5.179). Return ONLY JSON with all numbers you see:
{"pump_price":5.359, "app_price":4.80, "gallons":69.74, "sale":361.18, "diesel_gallons":69.74, "diesel_price":5.179, "location":"TA Grand Island"}
Fix 36 1.18 -> 361.18. Only JSON.'''
    try:
        r=client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],
            max_tokens=200
        )
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT OK: {txt}")
        return json.loads(txt)
    except Exception as e:
        err=str(e)
        print(f"GPT ERROR: {err}")
        traceback.print_exc()
        return {"error": err}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    key_status = "ключ есть ✅" if OPENAI_KEY else "ключа НЕТ ❌ добавь OPENAI_API_KEY в Railway!"
    await u.message.reply_text(f"v9 GPT готов! {key_status}\nКидай любые фото, даже по 3 в одном сообщении — читаю всё!")

async def handle(uid, data, upd):
    if "error" in data:
        await upd.message.reply_text(f"Ошибка OpenAI (ты оплатил, но ключ не работает):\n{data['error'][:500]}\n\nПроверь в Railway Variables OPENAI_API_KEY и баланс на platform.openai.com")
        return

    now=time.time()
    if uid not in user_data or now-user_data[uid]['t']>300:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now

    for k in ["pump_price","app_price","diesel_price"]:
        if k in data: user_data[uid]['prices'].append(float(data[k]))
    for k in ["gallons","diesel_gallons"]:
        if k in data: user_data[uid]['gals'].append(float(data[k]))
    if "sale" in data and float(data["sale"])>100:
        # sale / gallons = pump_price
        if user_data[uid]['gals']:
            g=max(user_data[uid]['gals'])
            if g>0: user_data[uid]['prices'].append(float(data["sale"])/g)
    if "location" in data: user_data[uid]['loc']=str(data["location"])

    prices=user_data[uid]['prices']
    gals=user_data[uid]['gals']
    print(f"STATE {prices} {gals}")

    if len(prices)>=2 and len(gals)>=1:
        pump=max(prices)
        app=min(prices)
        gal=max(gals)
        disc=pump-app
        if disc<0: disc=-disc
        save=disc*gal
        add_fuel(uid, gal, disc, save, user_data[uid]['loc'])
        await upd.message.reply_text(
            f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
            f"📍 {user_data[uid]['loc']}\n"
            f"⛽ {gal:.3f} gal\n"
            f"💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n"
            f"💰 Экономия ${save:.2f}"
        )
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':0}
    else:
        await upd.message.reply_text(f"Прочитал: {data}\nСобрал: цены {prices} gal {gals}\nКинь еще фото (карту $4.80 или колонку)")

async def photo(u,c):
    try:
        uid=u.effective_user.id
        f=await u.message.photo[-1].get_file()
        b=bytes(await f.download_as_bytearray())
        data=await ask_gpt(b)
        await handle(uid, data, u)
    except Exception as e:
        print(f"PHOTO HANDLER CRASH {e}")
        traceback.print_exc()
        await u.message.reply_text(f"Упал обработчик фото: {e}")

async def text_msg(u,c):
    t=u.message.text
    nums=[float(x) for x in re.findall(r"\d+\.\d+", t.replace('$',' ').replace(',','.'))]
    if nums:
        d={}
        small=[n for n in nums if 2.5<=n<=6.6]
        big=[n for n in nums if 10<=n<=150]
        if small: d["app_price"]=min(small)
        if len(small)>=2: d["pump_price"]=max(small)
        if big: d["gallons"]=max(big)
        await handle(u.effective_user.id, d, u)

async def report_cmd(u,c):
    cnt,gal,save=get_weekly(u.effective_user.id)
    if cnt==0: await u.message.reply_text("Нет заправок на неделе")
    else: await u.message.reply_text(f"📊 С понедельника:\n⛽ {cnt}\n🛢️ {gal:.1f}\n💰 ${save:.2f}")

async def weekly_job(ctx):
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT user_id, chat_id FROM users")
    for uid,cid in cur.fetchall():
        cnt,gal,save=get_weekly(uid)
        if cnt>0:
            try: await ctx.bot.send_message(cid, text=f"📊 ПЯТНИЦА 8AM CT:\n⛽ {cnt}\n🛢️ {gal:.1f}\n💰 ${save:.2f}")
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
    print("v9 GPT started")
    app.run_polling()
