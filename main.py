import os, re, base64, datetime, sqlite3, json, asyncio
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN=os.environ.get("BOT_TOKEN")
client=OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB="fuel.db"
CENTRAL=ZoneInfo("America/Chicago")

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.commit(); con.close()
def add_fuel(uid,gal,disc,save,loc=""):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",(uid,datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"),gal,disc,save,loc))
    con.commit(); con.close()
def get_weekly(uid):
    con=sqlite3.connect(DB)
    today=datetime.datetime.now(CENTRAL).date()
    last_fri=today-datetime.timedelta(days=(today.weekday()-4)%7)
    next_fri=last_fri+datetime.timedelta(days=7)
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=? AND date<?",(uid,str(last_fri),str(next_fri)))
    r=cur.fetchone()
    cur.execute("SELECT SUM(gallons), SUM(saving) FROM fuel WHERE user_id=?",(uid,))
    r2=cur.fetchone(); con.close()
    return (r[0],r[1] or 0,r[2] or 0) if r and r[0] else (0,0,0),(r2[0] or 0,r2[1] or 0),last_fri,next_fri

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='ONLY JSON, this image only: pump_price 5.439, diesel_price, pump_gallons 120.553, app_price 4.94 or 4.80. Only JSON.'
    r=client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=100)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    try: return json.loads(txt)
    except: return {}

# НОВОЕ: файлы храним в самом job, а не в общем dict
pending={}

async def album_job(context):
    data=context.job.data
    gid=data['gid']; uid=data['uid']; chat_id=data['chat_id']; files=data['files']
    # удаляем из pending если это последний job для этого gid
    pending.pop(gid,None)

    results=[]
    for b in files:
        results.append(await ask_gpt(b))

    pump=diesel=app=gal=None
    has_pump=has_receipt=False
    for d in results:
        if "pump_price" in d:
            try:
                v=float(str(d["pump_price"]).replace(',','.'))
                if 2.5<=v<=7.5: pump=v; has_pump=True
            except: pass
        if "diesel_price" in d:
            try:
                v=float(str(d["diesel_price"]).replace(',','.'))
                if 2.5<=v<=7.5: diesel=v; has_receipt=True
            except: pass
        if "app_price" in d:
            try:
                v=float(str(d["app_price"]).replace(',','.'))
                if 2.5<=v<=7.5: app=v
            except: pass
        for k in ["diesel_gallons","pump_gallons","gallons"]:
            if k in d:
                try:
                    v=float(str(d[k]).replace(',','.'))
                    if 1<=v<=300: gal=v
                except: pass

    if gal is None or app is None or (pump is None and diesel is None): return
    base=pump if has_pump and pump is not None else diesel
    label=f"Колонка {base:.3f}"
    disc=base-app; save=disc*gal
    add_fuel(uid,gal,disc,save,"TA Grand Island")
    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL (без DEF)\n{label}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal ({base:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")

async def photo(u,c):
    uid=u.effective_user.id
    gid=u.message.media_group_id or f"s_{u.message.message_id}"
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())

    # собираем файлы для этого gid
    if gid not in pending:
        pending[gid]={'uid':uid,'chat_id':u.effective_chat.id,'files':[]}
    pending[gid]['files'].append(b)

    # отменяем старый таймер для этого же gid и ставим новый с АКТУАЛЬНЫМ списком файлов
    for j in c.job_queue.get_jobs_by_name(f"a{gid}"): j.schedule_removal()
    c.job_queue.run_once(album_job, 1.0, data={'gid':gid,'uid':uid,'chat_id':u.effective_chat.id,'files':list(pending[gid]['files'])}, name=f"a{gid}")

async def start(u,c):
    pending.clear()
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v30 - без старых, каждый коллаж отдельно")
async def clear_cmd(u,c):
    pending.clear()
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")
async def report_cmd(u,c):
    (cnt,gal,save),(total_gal,total_save),last_fri,next_fri=get_weekly(u.effective_user.id)
    await c.bot.send_message(chat_id=u.effective_chat.id,text=f"📊 ОТЧЕТ | Пт-Пт {last_fri.strftime('%m/%d')} - {next_fri.strftime('%m/%d')} CT\nВсего скидок: ${total_save:.2f} | {total_gal:.1f} gal\nС {last_fri.strftime('%m/%d')}: {cnt} зап • {gal:.1f} gal • ${save:.2f}")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.run_polling()
