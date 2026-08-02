import os, re, base64, datetime, sqlite3, json, asyncio
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
    r=cur.fetchone()
    cur.execute("SELECT SUM(gallons), SUM(saving) FROM fuel WHERE user_id=?", (uid,))
    r2=cur.fetchone()
    con.close()
    week = (r[0], r[1] or 0, r[2] or 0) if r and r[0] else (0,0,0)
    total = (r2[0] or 0, r2[1] or 0) if r2 else (0,0)
    return week, total

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='''Return ONLY JSON. Look carefully:
- pump_price: LED "5.439" with "PRICE PER GALLON" or "DIESEL"
- pump_gallons: LED "20.553" with "Gallons" label, even if blurry
- diesel_price: receipt "69.74 at 5.179" -> 5.179
- diesel_gallons: receipt "69.74 gallons"
- app_price: map bubble "$4.94" or "$4.80" - take cheapest if many
- total: pump total "$548.45" ignore
Examples:
pump shows 5.439 and 20.553 gal + map 4.94 -> {"pump_price":5.439,"pump_gallons":20.553,"app_price":4.94}
receipt 69.74 at 5.179 + map 4.80 -> {"diesel_price":5.179,"diesel_gallons":69.74,"app_price":4.80}
Only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=200)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERR {e}")
        return {}

album_buffer={}
album_lock=asyncio.Lock()

async def clear_all(uid, chat_id, context):
    async with album_lock: album_buffer.clear()
    await context.bot.send_message(chat_id=chat_id, text="✅ Память очищена!")

async def process(uid, chat_id, all_data, context):
    try:
        pump=diesel=app=gal=None
        loc="Love's / TA"
        has_pump=False

        for d in all_data:
            if "pump_price" in d:
                try:
                    v=float(str(d["pump_price"]).replace(',','.'))
                    if 2.5<=v<=7.5:
                        pump=v
                        has_pump=True
                except: pass
            if "diesel_price" in d:
                try:
                    v=float(str(d["diesel_price"]).replace(',','.'))
                    if 2.5<=v<=7.5: diesel=v
                except: pass
            if "app_price" in d:
                try:
                    v=float(str(d["app_price"]).replace(',','.'))
                    if 2.5<=v<=7.5:
                        if app is None or v < app: app=v # берем самую дешевую
                except: pass
            for k in ["diesel_gallons","pump_gallons","gallons"]:
                if k in d:
                    try:
                        v=float(str(d[k]).replace(',','.'))
                        if 1<=v<=200: gal=v
                    except: pass
            if "location" in d and d["location"]: loc=d["location"]

        print(f"FINAL has_pump={has_pump} pump={pump} diesel={diesel} app={app} gal={gal}")

        if app is None:
            await context.bot.send_message(chat_id=chat_id, text=f"Не вижу цену с карты. Нашел: pump={pump} diesel={diesel} gal={gal} app={app}")
            return
        if gal is None:
            await context.bot.send_message(chat_id=chat_id, text=f"Не вижу галлоны. Нашел: pump={pump} diesel={diesel} gal={gal} app={app}")
            return

        if has_pump and pump is not None:
            base=pump
            label=f"Колонка {pump:.3f}"
        elif diesel is not None:
            base=diesel
            label=f"Чек {diesel:.3f}"
        else:
            await context.bot.send_message(chat_id=chat_id, text="Не вижу цену колонки/чека")
            return

        disc=base-app
        save=disc*gal
        add_fuel(uid, gal, disc, save, loc)

        await context.bot.send_message(chat_id=chat_id,
            text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
                 f"📍 {loc}\n"
                 f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
                 f"{label}, Карта: {app:.3f}\n"
                 f"💸 Скидка ${disc:.3f}/gal ({base:.3f} - {app:.3f})\n"
                 f"💰 Экономия ${save:.2f}")
    except Exception as e:
        print(f"PROCESS ERR {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Ошибка: {e}")

async def album_job(context):
    gid=context.job.data['gid']
    async with album_lock:
        if gid not in album_buffer: return
        data=album_buffer.pop(gid)
    res=[]
    for b in data['files']: res.append(await ask_gpt(b))
    await process(data['uid'], data['chat_id'], res, context)

async def photo(u,c):
    uid=u.effective_user.id
    gid=u.message.media_group_id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    if gid:
        async with album_lock:
            if gid not in album_buffer: album_buffer[gid]={'uid':uid,'chat_id':u.effective_chat.id,'files':[]}
            album_buffer[gid]['files'].append(b)
        for j in c.job_queue.get_jobs_by_name(f"a{gid}"): j.schedule_removal()
        c.job_queue.run_once(album_job, 2.5, data={'gid':gid}, name=f"a{gid}")
    else:
        r=await ask_gpt(b)
        await process(uid, u.effective_chat.id, [r], c)

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    await clear_all(u.effective_user.id, u.effective_chat.id, c)
    await c.bot.send_message(chat_id=u.effective_chat.id, text="✅ v21 готов! Если молчит - напишет что не видит.\n/report - отчет")

async def clear_cmd(u,c): await clear_all(u.effective_user.id, u.effective_chat.id, c)
async def report_cmd(u,c):
    (cnt,gal,save),(total_gal,total_save)=get_weekly(u.effective_user.id)
    await c.bot.send_message(chat_id=u.effective_chat.id, text=f"📊 ОТЧЕТ\n\nС понедельника:\n⛽ {cnt} зап\n🛢️ {gal:.1f} gal\n💰 ${save:.2f}\n\nВсего:\n🛢️ {total_gal:.1f} gal\n💰 ${total_save:.2f}")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(CommandHandler("Report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.COMMAND, clear_cmd))
    print("v21 FIXED")
    app.run_polling()
