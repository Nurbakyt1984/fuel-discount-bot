import os, re, base64, time, datetime, sqlite3, json, traceback, asyncio
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
    r=cur.fetchone(); con.close()
    return (r[0], r[1] or 0, r[2] or 0) if r and r[0] else (0,0,0)

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    # Четко разделяем что есть что
    prompt='''Return ONLY JSON with what you see:
Pump display "$ 5.359" -> {"pump_price":5.359}
Receipt "DIESEL 69.74 at 5.179" -> {"diesel_price":5.179, "diesel_gallons":69.74}
Map green bubble "$4.80" -> {"app_price":4.80}
Location "TA Grand Island" -> {"location":"TA Grand Island"}
IGNORE DEF 2.916 at 4.899. Only JSON.'''
    try:
        resp=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=resp.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        return {"error":str(e)}

album_buffer = {}
album_lock = asyncio.Lock()
last_gallons = {} # uid -> (gal, timestamp)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    await u.message.reply_text("✅ v14 - 1 ответ, считает правильно 5.359-4.80")

async def process_and_reply(uid, chat_id, all_data, context):
    pump=None
    diesel_price=None
    app_price=None
    gal=None
    loc="TA Grand Island"

    for data in all_data:
        if "error" in data: continue
        if "pump_price" in data:
            try: pump=float(str(data["pump_price"]).replace(',','.'))
            except: pass
        if "diesel_price" in data:
            try: diesel_price=float(str(data["diesel_price"]).replace(',','.'))
            except: pass
        if "app_price" in data:
            try: app_price=float(str(data["app_price"]).replace(',','.'))
            except: pass
        if "diesel_gallons" in data or "gallons" in data:
            try:
                v=data.get("diesel_gallons") or data.get("gallons")
                gal=float(str(v).replace(',','.'))
            except: pass
        if "location" in data and data["location"]:
            loc=data["location"]

    # Галлоны могут быть с прошлого чека - берем последние 10 минут
    if gal is None and uid in last_gallons and time.time()-last_gallons[uid][1] < 600:
        gal=last_gallons[uid][0]
    if gal:
        last_gallons[uid]=(gal, time.time())

    print(f"COMBINED uid={uid} pump={pump} diesel={diesel_price} app={app_price} gal={gal}")

    if gal is None or app_price is None:
        return # Не хватает данных - молчим, не спамим

    # ПРАВИЛЬНАЯ ЛОГИКА СКИДКИ:
    # Если есть колонка 5.359 и карта 4.80 -> скидка 5.359-4.80
    # Если нет колонки, но есть чек 5.179 и карта 4.80 -> скидка 5.179-4.80
    if pump is not None:
        base_price=pump
    else:
        base_price=diesel_price

    if base_price is None:
        return

    disc=base_price - app_price
    if disc < 0.05: # Защита от 5.359-5.179=0.18 когда карту не прочитал
        return

    save=disc*gal
    add_fuel(uid, gal, disc, save, loc)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
             f"📍 {loc}\n"
             f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
             f"Колонка: {base_price:.3f}, Карта: {app_price:.3f}\n"
             f"💸 Скидка ${disc:.3f}/gal ({base_price:.3f} - {app_price:.3f})\n"
             f"💰 Экономия ${save:.2f}"
    )

async def album_job(context: ContextTypes.DEFAULT_TYPE):
    group_id = context.job.data['group_id']
    async with album_lock:
        if group_id not in album_buffer:
            return
        data = album_buffer.pop(group_id)
    all_gpt=[]
    for b in data['files']:
        all_gpt.append(await ask_gpt(b))
    await process_and_reply(data['uid'], data['chat_id'], all_gpt, context)

async def photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid=u.effective_user.id
    chat_id=u.effective_chat.id
    group_id=u.message.media_group_id
    file=await u.message.photo[-1].get_file()
    b=bytes(await file.download_as_bytearray())

    if group_id:
        async with album_lock:
            if group_id not in album_buffer:
                album_buffer[group_id]={'uid':uid, 'chat_id':chat_id, 'files':[]}
            album_buffer[group_id]['files'].append(b)
        for job in c.job_queue.get_jobs_by_name(f"album_{group_id}"):
            job.schedule_removal()
        c.job_queue.run_once(album_job, 2, data={'group_id':group_id}, name=f"album_{group_id}")
        return
    else:
        data=await ask_gpt(b)
        await process_and_reply(uid, chat_id, [data], c)

async def report_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    cnt,gal,save=get_weekly(u.effective_user.id)
    await u.message.reply_text(f"📊 С понедельника: {cnt} зап, {gal:.1f} gal, ${save:.2f}" if cnt else "Нет заправок")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v14 FINAL")
    app.run_polling()
