import os, re, base64, datetime, sqlite3, json, traceback, asyncio
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

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='''Return ONLY JSON. Find:
- pump_price: LED on pump "5.359"
- diesel_price: receipt "69.74 gallons at 5.179/gal" -> 5.179
- diesel_gallons: same line -> 69.74
- app_price: map bubble "$4.80" -> 4.80
- location: "TA Grand Island"
IGNORE DEF. Only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        return json.loads(txt)
    except Exception as e:
        return {"error":str(e)}

album_buffer={}
album_lock=asyncio.Lock()

async def process(uid, chat_id, all_data, context):
    pump=diesel=app=gal=None
    loc=""
    for d in all_data:
        if "pump_price" in d: pump=float(str(d["pump_price"]).replace(',','.'))
        if "diesel_price" in d: diesel=float(str(d["diesel_price"]).replace(',','.'))
        if "app_price" in d: app=float(str(d["app_price"]).replace(',','.'))
        if "diesel_gallons" in d: gal=float(str(d["diesel_gallons"]).replace(',','.'))
        if "gallons" in d: gal=float(str(d["gallons"]).replace(',','.'))
        if "location" in d: loc=d["location"]

    if app is None or gal is None:
        return

    # ЧТО НА ФОТО - ТО И СЧИТАЕМ, НИЧЕГО ИЗ ПРОШЛОГО
    if pump is not None:
        base=pump
        base_name=f"Колонка {pump:.3f}"
    else:
        base=diesel
        base_name=f"Чек {diesel:.3f}"
    
    if base is None:
        return

    disc=base-app
    if disc < 0.02:
        return
    save=disc*gal
    add_fuel(uid, gal, disc, save, loc)

    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
             f"📍 {loc}\n"
             f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
             f"{base_name}, Карта: {app:.3f}\n"
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
        c.job_queue.run_once(album_job, 2.2, data={'gid':gid}, name=f"a{gid}")
    else:
        r=await ask_gpt(b)
        await process(uid, u.effective_chat.id, [r], c)

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    await u.message.reply_text("✅ v16 - не помню старое. Что на фото, то и считаю.")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v16")
    app.run_polling()
