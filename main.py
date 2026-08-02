import os, re, base64, time, datetime, sqlite3, json, traceback
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.commit()
    con.close()

def add_fuel(uid, gal, disc, save, loc=""):
    con=sqlite3.connect(DB)
    dt=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (uid, dt, gal, disc, save, loc))
    con.commit()
    con.close()

def get_weekly(uid):
    con=sqlite3.connect(DB)
    today=datetime.date.today()
    monday=today - datetime.timedelta(days=today.weekday())
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=?", (uid, str(monday)))
    r=cur.fetchone()
    con.close()
    if not r or r[0] is None:
        return (0,0,0)
    return (r[0], r[1] or 0, r[2] or 0)

async def ask_gpt(b):
    if not client:
        return {"error":"NO_OPENAI_KEY"}
    b64=base64.b64encode(b).decode()
    prompt='''Read fuel receipt and map. Return ONLY JSON.
If you see TA receipt: {"diesel_gallons":69.74, "diesel_price":5.179, "location":"TA Grand Island"}
If you see map green bubble $4.80: {"app_price":4.80}
If you see pump display 5.359: {"pump_price":5.359}
If collage has both receipt and map: {"diesel_gallons":69.74, "diesel_price":5.179, "app_price":4.80, "location":"TA Grand Island"}
Rules:
- DIESEL line is 69.74 gallons at 5.179/gal
- IGNORE DEF line 2.916 gallons at 4.899/gal completely, never return it
- Green bubble $4.80 on map = app_price
- Only JSON, no text'''
    try:
        resp=client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],
            max_tokens=200
        )
        txt=resp.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m:
            txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERROR {e}")
        traceback.print_exc()
        return {"error":str(e)}

# Память только на 3 минуты для сбора коллажа
user_data={}

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit()
    con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':time.time()}
    await u.message.reply_text(
        "✅ v11 готов!\n"
        "Фиксы:\n"
        "- Не беру старые цены (5.359 пропал)\n"
        "- DEF игнорирую полностью\n"
        "- Читаю 5.179 с чека и 4.80 с карты\n"
        "- Понимаю 5,179 с запятой\n\n"
        "Кидай чек + карту. /report - отчет."
    )

async def handle(uid, data, upd: Update):
    if "error" in data:
        await upd.message.reply_text(f"Ошибка OpenAI: {data['error'][:400]}")
        return

    now=time.time()
    # Сбрасываем если прошло 3 минуты
    if uid not in user_data or now-user_data[uid]['t']>180:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now

    # Собираем только из текущего запроса
    for k in ["pump_price","app_price","diesel_price"]:
        if k in data and data[k] is not None:
            try:
                v=float(str(data[k]).replace(',','.'))
                if 2.5<=v<=6.6 and abs(v-4.899)>0.05 and abs(v-2.916)>0.05:
                    if not any(abs(v-x)<0.005 for x in user_data[uid]['prices']):
                        user_data[uid]['prices'].append(v)
            except:
                pass

    for k in ["diesel_gallons","gallons"]:
        if k in data and data[k] is not None:
            try:
                v=float(str(data[k]).replace(',','.'))
                if 10<=v<=150 and abs(v-2.916)>0.05:
                    if not any(abs(v-x)<0.005 for x in user_data[uid]['gals']):
                        user_data[uid]['gals'].append(v)
            except:
                pass

    if "location" in data and data["location"]:
        user_data[uid]['loc']=str(data["location"])

    prices=user_data[uid]['prices']
    gals=user_data[uid]['gals']

    print(f"STATE uid={uid} prices={prices} gals={gals}")

    # Убираем дубликаты
    uniq=[]
    for p in prices:
        if not any(abs(p-x)<0.01 for x in uniq):
            uniq.append(p)

    if len(uniq)>=2 and len(gals)>=1:
        pump=max(uniq)
        app_price=min(uniq)
        gal=max(gals)
        disc=pump-app_price
        save=disc*gal
        add_fuel(uid, gal, disc, save, user_data[uid]['loc'])

        # Красивое сообщение
        if abs(pump-5.179)<0.01 and abs(app_price-4.80)<0.01:
            # Только чек + карта, без колонки
            text=(
                f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
                f"📍 {user_data[uid]['loc']}\n"
                f"⛽ {gal:.3f} gal DIESEL @ {pump:.3f} (без DEF)\n"
                f"Карта: {app_price:.3f}\n"
                f"💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app_price:.3f})\n"
                f"💰 Экономия ${save:.2f}"
            )
        else:
            text=(
                f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
                f"📍 {user_data[uid]['loc']}\n"
                f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
                f"Колонка: {pump:.3f}, Карта: {app_price:.3f}\n"
                f"💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app_price:.3f})\n"
                f"💰 Экономия ${save:.2f}"
            )
        await upd.message.reply_text(text)
        # Полный сброс после успеха
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':0}
    else:
        if len(uniq)>=1 or len(gals)>=1:
            await upd.message.reply_text(f"Прочитал: {data}\nСобрал: цены {uniq} gal {gals}\nКинь еще фото (карту $4.80) в течение 3 мин.")

async def photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        uid=u.effective_user.id
        file=await u.message.photo[-1].get_file()
        b=bytes(await file.download_as_bytearray())
        data=await ask_gpt(b)
        await handle(uid, data, u)
    except Exception as e:
        traceback.print_exc()
        await u.message.reply_text(f"Упал: {e}")

async def text_msg(u: Update, c: ContextTypes.DEFAULT_TYPE):
    t=u.message.text or ""
    # Поддержка и точки и запятых: 5.179 и 5,179
    raw=re.findall(r"\d+[.,]\d+", t)
    nums=[]
    for r in raw:
        try:
            v=float(r.replace(',','.'))
            nums.append(v)
        except:
            pass
    if not nums:
        return
    d={}
    small=[n for n in nums if 2.5<=n<=6.6 and abs(n-4.899)>0.05 and abs(n-2.916)>0.05]
    big=[n for n in nums if 10<=n<=150 and abs(n-2.916)>0.05]
    if small:
        if len(small)==1:
            d["app_price"]=small[0]
        else:
            d["app_price"]=min(small)
            d["pump_price"]=max(small)
    if big:
        d["diesel_gallons"]=max(big)
    await handle(u.effective_user.id, d, u)

async def report_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cnt,gal,save=get_weekly(u.effective_user.id)
    if cnt==0:
        await u.message.reply_text("На этой неделе заправок нет")
    else:
        await u.message.reply_text(f"📊 Отчет с понедельника:\n⛽ Заправок: {cnt}\n🛢️ Галлонов: {gal:.1f}\n💰 Экономия: ${save:.2f}")

async def clear_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    await u.message.reply_text("Память очищена! 5.359 удален. Кидай заново.")

async def weekly_job(ctx):
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT user_id, chat_id FROM users")
    for uid,cid in cur.fetchall():
        cnt,gal,save=get_weekly(uid)
        if cnt>0:
            try:
                await ctx.bot.send_message(chat_id=cid, text=f"📊 ОТЧЕТ ПЯТНИЦА 8AM CT:\n⛽ Заправок: {cnt}\n🛢️ Всего: {gal:.1f} gal\n💰 Скидка: ${save:.2f}")
            except:
                pass
    con.close()

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    app.job_queue.run_daily(weekly_job, time=datetime.time(hour=13, minute=0, second=0), days=(4,))
    print("v11 started")
    app.run_polling()
