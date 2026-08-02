import os, re, base64, time, datetime, sqlite3, json, traceback
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
    if not client: return {"error":"NO_KEY"}
    b64=base64.b64encode(b).decode()
    prompt='''You are fuel OCR. IGNORE DEF completely. Return ONLY JSON:
{"pump_price":5.359, "app_price":4.80, "gallons":69.74, "diesel_gallons":69.74, "diesel_price":5.179, "location":"TA Grand Island"}
Rules: Never return DEF, 2.916, 4.899. Only DIESEL line. Fix 36 1.18->361.18. Only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERR {e}")
        return {"error":str(e)}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    await u.message.reply_text("v9.1 без DEF готов! Кидай чек - DEF игнорирую")

async def handle(uid, data, upd):
    if "error" in data:
        await upd.message.reply_text(f"OpenAI ошибка: {data['error'][:400]}")
        return
    now=time.time()
    if uid not in user_data or now-user_data[uid]['t']>300:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now

    # Добавляем только DIESEL, DEF уже отфильтрован GPT, но еще фильтруем тут
    for k in ["pump_price","app_price","diesel_price"]:
        if k in data:
            v=float(data[k])
            if abs(v-4.899)>0.05 and abs(v-2.916)>0.05: # фильтр DEF
                user_data[uid]['prices'].append(v)
    for k in ["gallons","diesel_gallons"]:
        if k in data:
            v=float(data[k])
            if abs(v-2.916)>0.05:
                user_data[uid]['gals'].append(v)
    if "location" in data: user_data[uid]['loc']=str(data["location"])

    prices=user_data[uid]['prices']
    gals=user_data[uid]['gals']
    print(f"STATE no DEF: {prices} {gals}")

    if len(prices)>=2 and len(gals)>=1:
        pump=max(prices); app=min(prices); gal=max(gals)
        disc=pump-app; save=disc*gal
        add_fuel(uid, gal, disc, save, user_data[uid]['loc'])
        await upd.message.reply_text(f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n📍 {user_data[uid]['loc']}\n⛽ {gal:.3f} gal (только DIESEL, без DEF)\n💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':0}
    else:
        await upd.message.reply_text(f"Прочитал: {data} (DEF проигнорирован)\nСобрал: цены {prices} gal {gals}")

async def photo(u,c):
    try:
        f=await u.message.photo[-1].get_file()
        b=bytes(await f.download_as_bytearray())
        data=await ask_gpt(b)
        await handle(u.effective_user.id, data, u)
    except Exception as e:
        await u.message.reply_text(f"Ошибка: {e}")

async def text_msg(u,c):
    nums=[float(x) for x in re.findall(r"\d+\.\d+", u.message.text.replace('$',' '))]
    if nums:
        d={}
        small=[n for n in nums if 2.5<=n<=6.6 and abs(n-4.899)>0.05]
        big=[n for n in nums if 10<=n<=150 and abs(n-2.916)>0.05]
        if small: d["app_price"]=min(small)
        if len(small)>=2: d["pump_price"]=max(small)
        if big: d["gallons"]=max(big)
        await handle(u.effective_user.id, d, u)

async def report_cmd(u,c):
    cnt,gal,save=get_weekly(u.effective_user.id)
    await u.message.reply_text(f"📊 С понедельника:\n⛽ {cnt}\n🛢️ {gal:.1f}\n💰 ${save:.2f}" if cnt else "Нет заправок")

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
    print("v9.1 no DEF started")
    app.run_polling()
