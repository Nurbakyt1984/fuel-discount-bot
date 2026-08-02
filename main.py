import os, re, base64, datetime, sqlite3, json, asyncio, time
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

async def ask_gpt(b, focus="all"):
    b64=base64.b64encode(b).decode()
    if focus=="gallons":
        prompt='SMALL LCD with 2 numbers stacked. Bottom is GALLONS. Read carefully: 120.553 -> {"pump_gallons":120.553} 69.740 -> {"pump_gallons":69.74} Return ONLY JSON.'
    else:
        prompt='Return ONLY JSON. Read exactly what you see, do NOT use memory:\n- pump_price: big LED "5.439" or "5.359" - look at last digit! 9 vs 9 vs 3\n- pump_gallons: small LCD bottom number "120.553" or "69.740"\n- app_price: map bubble "$4.94" - read number in green bubble, do NOT invent 4.80\nExample current image: 5.439 + 120.553 + map 4.94 -> {"pump_price":5.439,"pump_gallons":120.553,"app_price":4.94}\nOnly JSON.'

    r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=200)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}', txt, re.DOTALL)
    if m: txt=m.group(0)
    print(f"GPT {focus}: {txt}")
    try: return json.loads(txt)
    except: return {}

album_buffer={}
album_lock=asyncio.Lock()

async def clear_all(uid, chat_id, context):
    async with album_lock: album_buffer.clear()
    await context.bot.send_message(chat_id=chat_id, text="✅ Память очищена!")

async def process(uid, chat_id, all_data, context, raw_files):
    # ПОЛНОСТЬЮ СТАРТ С НУЛЯ - НИКАКИХ СТАРЫХ
    pump=diesel=app=gal=None
    loc="TA Grand Island"
    has_pump=False

    for d in all_data:
        if "pump_price" in d:
            try:
                v=float(str(d["pump_price"]).replace(',','.'))
                if 2.5<=v<=7.5: pump=v; has_pump=True
            except: pass
        if "diesel_price" in d:
            try:
                v=float(str(d["diesel_price"]).replace(',','.'))
                if 2.5<=v<=7.5: diesel=v
            except: pass
        if "app_price" in d:
            try:
                v=float(str(d["app_price"]).replace(',','.'))
                if 2.5<=v<=8.0:
                    if app is None or v!=4.8: # приоритет новой цене, не 4.80
                        app=v
                    if app is None: app=v
            except: pass
        for k in ["diesel_gallons","pump_gallons","gallons"]:
            if k in d:
                try:
                    v=float(str(d[k]).replace(',','.'))
                    if 1<=v<=200: gal=v
                except: pass

    if gal is None and raw_files:
        for b in raw_files:
            d2 = await ask_gpt(b, focus="gallons")
            for k in ["diesel_gallons","pump_gallons","gallons"]:
                if k in d2:
                    try:
                        v=float(str(d2[k]).replace(',','.'))
                        if 1<=v<=200: gal=v
                    except: pass
            if gal is not None: break

    print(f"FINAL pump={pump} app={app} gal={gal}")

    if app is None or gal is None:
        await context.bot.send_message(chat_id=chat_id, text=f"Не вижу: pump={pump} gal={gal} app={app} - кинь еще раз четче")
        return

    base=pump if has_pump and pump else diesel
    if base is None: return
    label=f"Колонка {base:.3f}"

    disc=base-app
    save=disc*gal
    add_fuel(uid, gal, disc, save, loc)

    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now().strftime('%m/%d/%Y')}\n📍 {loc}\n⛽ {gal:.3f} gal DIESEL (без DEF)\n{label}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal ({base:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")

async def album_job(context):
    gid=context.job.data['gid']
    async with album_lock:
        if gid not in album_buffer: return
        data=album_buffer.pop(gid)
        # ЧИСТИМ ВЕСЬ СТАРЫЙ МУСОР СТАРШЕ 5 СЕК
        now=time.time()
        for k in list(album_buffer.keys()):
            if now - album_buffer[k].get('ts',0) > 5:
                del album_buffer[k]
    res=[]
    for b in data['files']: res.append(await ask_gpt(b))
    await process(data['uid'], data['chat_id'], res, context, data['files'])

async def photo(u,c):
    uid=u.effective_user.id
    gid=u.message.media_group_id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    if gid:
        async with album_lock:
            if gid not in album_buffer:
                album_buffer[gid]={'uid':uid,'chat_id':u.effective_chat.id,'files':[],'ts':time.time()}
            album_buffer[gid]['files'].append(b)
            album_buffer[gid]['ts']=time.time()
        for j in c.job_queue.get_jobs_by_name(f"a{gid}"): j.schedule_removal()
        c.job_queue.run_once(album_job, 2.0, data={'gid':gid}, name=f"a{gid}")
    else:
        async with album_lock: album_buffer.clear() # одиночное фото - чистим все альбомы
        r=await ask_gpt(b)
        await process(uid, u.effective_chat.id, [r], c, [b])

async def start(u,c):
    await clear_all(u.effective_user.id, u.effective_chat.id, c)
    await c.bot.send_message(chat_id=u.effective_chat.id, text="✅ v23 фикс старых данных!")
async def clear_cmd(u,c): await clear_all(u.effective_user.id, u.effective_chat.id, c)
async def report_cmd(u,c):
    (cnt,gal,save),(total_gal,total_save)=get_weekly(u.effective_user.id)
    text=f"📊 ОТЧЕТ | Получено скидок: ${total_save:.2f} | Заправлено: {total_gal:.1f} галлон\n\nС понедельника: {cnt} зап • {gal:.1f} gal • ${save:.2f}\nВсего: {total_gal:.1f} gal • ${total_save:.2f}"
    await c.bot.send_message(chat_id=u.effective_chat.id, text=text)

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v23 NO OLD DATA")
    app.run_polling()
