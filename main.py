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
    con.commit(); con.close()

def add_fuel(uid, gal, disc, save, loc=""):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (uid, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), gal, disc, save, loc))
    con.commit(); con.close()

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='''Return ONLY JSON. Strict rules:
- pump_price ONLY if you see LED pump display with "5.359" - if you see only receipt, DO NOT return pump_price
- diesel_price: from receipt "69.74 gallons at 5.179/gal" -> 5.179
- diesel_gallons: 69.74
- app_price: map bubble "$4.80" -> 4.80
If image is receipt+map collage, return {"diesel_price":5.179,"diesel_gallons":69.74,"app_price":4.80}
NEVER invent pump_price if no pump photo. Only JSON.'''
    r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}', txt, re.DOTALL)
    if m: txt=m.group(0)
    print(f"GPT: {txt}")
    try: return json.loads(txt)
    except: return {}

album_buffer={}
album_lock=asyncio.Lock()

async def clear_all(uid, chat_id, context):
    async with album_lock: album_buffer.clear()
    await context.bot.send_message(chat_id=chat_id, text="✅ Очищено! Кидай заново чек+карту.")

async def process(uid, chat_id, all_data, context):
    pump=diesel=app=gal=None
    loc="TA Grand Island"
    for d in all_data:
        if "pump_price" in d: 
            try: pump=float(str(d["pump_price"]).replace(',','.'))
            except: pass
        if "diesel_price" in d:
            try: diesel=float(str(d["diesel_price"]).replace(',','.'))
            except: pass
        if "app_price" in d:
            try: app=float(str(d["app_price"]).replace(',','.'))
            except: pass
        if "diesel_gallons" in d or "gallons" in d:
            try: gal=float(str(d.get("diesel_gallons") or d.get("gallons")).replace(',','.'))
            except: pass
        if "location" in d: loc=d["location"]

    print(f"COMBINE pump={pump} diesel={diesel} app={app} gal={gal}")

    if app is None or gal is None:
        return

    # ГЛАВНЫЙ ФИКС: если есть чек 5.179 - берем его, а не колонку 5.359
    if diesel is not None:
        base=diesel
        base_label=f"Чек {diesel:.3f}"
    elif pump is not None:
        base=pump
        base_label=f"Колонка {pump:.3f}"
    else:
        return

    disc=base-app
    save=disc*gal
    add_fuel(uid, gal, disc, save, loc)

    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n"
             f"📍 {loc}\n"
             f"⛽ {gal:.3f} gal DIESEL (без DEF)\n"
             f"{base_label}, Карта: {app:.3f}\n"
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

async def start(u,c): await clear_all(u.effective_user.id, u.effective_chat.id, c)
async def clear_cmd(u,c): await clear_all(u.effective_user.id, u.effective_chat.id, c)

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.COMMAND, clear_cmd))
    print("v18 FINAL FIX")
    app.run_polling()
