import os, re, base64, datetime, sqlite3, json
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
    prompt='''Return ONLY JSON. Strict:
- pump_price ONLY if you clearly see LED "$ 5.359" and "PRICE PER GALLON"
- diesel_price: receipt "69.74 gallons at 5.179/gal" -> 5.179
- diesel_gallons: 69.74
- pump_gallons: pump "69.740 Gallons" -> 69.74
- app_price: map green bubble "$4.80" -> 4.80
If receipt+map -> {"diesel_price":5.179,"diesel_gallons":69.74,"app_price":4.80}
If pump+gallons+map -> {"pump_price":5.359,"pump_gallons":69.74,"app_price":4.80}
Only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERR: {e}")
        return {}

album_buffer={}
album_lock=None
import asyncio
album_lock = asyncio.Lock()

async def clear_all(uid, chat_id, context):
    async with album_lock: album_buffer.clear()
    await context.bot.send_message(chat_id=chat_id, text="✅ Память очищена!")

async def process(uid, chat_id, all_data, context):
    pump=diesel=app=gal=None
    loc="TA Grand Island"
    has_pump_image=False

    for d in all_data:
        if "pump_price" in d and "diesel_gallons" not in d and "diesel_price" not in d:
            try:
                pump=float(str(d["pump_price"]).replace(',','.'))
                has_pump_image=True
            except: pass
        if "diesel_price" in d:
            try: diesel=float(str(d["diesel_price"]).replace(',','.'))
            except: pass
        if "app_price" in d:
            try: app=float(str(d["app_price"]).replace(',','.'))
            except: pass
        if "diesel_gallons" in d or "pump_gallons" in d or "gallons" in d:
            try: gal=float(str(d.get("diesel_gallons") or d.get("pump_gallons") or d.get("gallons")).replace(',','.'))
            except: pass
        if "location" in d: loc=d["location"]

    if app is None or gal is None:
        return

    if has_pump_image and pump is not None:
        base=pump
        label=f"Колонка {pump:.3f}"
    elif diesel is not None:
        base=diesel
        label=f"Чек {diesel:.3f}"
    else:
        return

    disc=base-app
    if disc < 0.01: return
    save=disc*gal
    add_fuel(uid, gal, disc, save, loc)

    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
             f"📍 {loc}\n"
             f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
             f"{label}, Карта: {app:.3f}\n"
             f"💸 Скидка ${disc:.3f}/gal ({base:.3f} - {app:.3f})\n"
             f"💰 Экономия ${save:.2f}")

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
    await c.bot.send_message(chat_id=u.effective_chat.id, text="✅ v20 готов!\nФоткай как хочешь.\n/report - отчет")

async def clear_cmd(u,c):
    await clear_all(u.effective_user.id, u.effective_chat.id, c)

async def report_cmd(u,c):
    (cnt,gal,save), (total_gal,total_save) = get_weekly(u.effective_user.id)
    await c.bot.send_message(chat_id=u.effective_chat.id,
        text=f"📊 ОТЧЕТ\n\n"
             f"С понедельника:\n"
             f"⛽ Заправок: {cnt}\n"
             f"🛢️ Галлонов: {gal:.1f}\n"
             f"💰 Сэкономил: ${save:.2f}\n\n"
             f"За все время:\n"
             f"🛢️ {total_gal:.1f} gal\n"
             f"💰 ${total_save:.2f}")

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
    print("v20 FINAL FULL")
    app.run_polling()
