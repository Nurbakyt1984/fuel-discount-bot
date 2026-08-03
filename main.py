import os, re, base64, datetime, sqlite3, json, asyncio, io
from zoneinfo import ZoneInfo
from PIL import Image
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB = "fuel.db"
CENTRAL = ZoneInfo("America/Chicago")

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.commit(); con.close()

def add_fuel(uid, gal, disc, save):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",
                (uid, datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"), gal, disc, save, "TA Grand Island"))
    con.commit(); con.close()

def get_weekly(uid):
    con=sqlite3.connect(DB)
    today=datetime.datetime.now(CENTRAL).date()
    last_fri=today-datetime.timedelta(days=(today.weekday()-4)%7)
    next_fri=last_fri+datetime.timedelta(days=7)
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=? AND date<?",
                (uid,str(last_fri),str(next_fri)))
    r=cur.fetchone()
    cur.execute("SELECT SUM(gallons), SUM(saving) FROM fuel WHERE user_id=?",(uid,))
    r2=cur.fetchone(); con.close()
    week=(r[0],r[1] or 0,r[2] or 0) if r and r[0] else (0,0,0)
    total=(r2[0] or 0,r2[1] or 0)
    return week,total,last_fri,next_fri

async def ask_gpt_fast(b):
    try:
        img=Image.open(io.BytesIO(b))
        img.thumbnail((900,900))
        buf=io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        b64=base64.b64encode(buf.getvalue()).decode()
    except:
        b64=base64.b64encode(b).decode()

    prompt='ONLY JSON. Read fuel: pump_price (green pump), gallons (120.553 or 69.74), app_price (map bubble $4.94). Return {"pump_price": number or null,"gallons": number or null,"app_price": number or null}. Only JSON.'
    def call():
        return client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"low"}}
            ]}], max_tokens=80)
    r=await asyncio.to_thread(call)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    try: return json.loads(txt)
    except: return {}

async def album_job(context):
    data=context.job.data
    uid=data['uid']; chat_id=data['chat_id']; files=data['files']
    results=await asyncio.gather(*[ask_gpt_fast(b) for b in files])

    pump=app=gal=None
    for d in results:
        if d.get('pump_price') or d.get('diesel_price'):
            try:
                v=float(str(d.get('pump_price') or d.get('diesel_price')).replace(',','.'))
                if 2.5<=v<=7.5: pump=v
            except: pass
        if d.get('app_price'):
            try:
                v=float(str(d['app_price']).replace(',','.'))
                if 2.5<=v<=7.5: app=v
            except: pass
        for k in ['gallons','diesel_gallons','pump_gallons']:
            if k in d and d[k] is not None:
                try:
                    v=float(str(d[k]).replace(',','.'))
                    if 1<=v<=400: gal=v
                except: pass

    if pump is None or app is None or gal is None:
        await context.bot.send_message(chat_id=chat_id, text=f"Не прочитал: {results}")
        return

    disc=pump-app; save=disc*gal
    add_fuel(uid,gal,disc,save)
    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL (без DEF)\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")

async def photo(u,c):
    uid=u.effective_user.id
    gid=u.message.media_group_id or f"s_{u.message.message_id}_{uid}"
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    old_jobs=c.job_queue.get_jobs_by_name(f"a{gid}")
    files=[b]
    if old_jobs:
        files = old_jobs[0].data['files'] + [b]
        for j in old_jobs: j.schedule_removal()
    c.job_queue.run_once(album_job, 0.8, data={'gid':gid,'uid':uid,'chat_id':u.effective_chat.id,'files':files}, name=f"a{gid}")

async def start(u,c):
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v33 FAST 2-3сек готов!")
async def clear_cmd(u,c):
    for jobs in c.job_queue.jobs():
        for j in jobs[1]: j.schedule_removal()
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")
async def report_cmd(u,c):
    week,total,last_fri,next_fri=get_weekly(u.effective_user.id)
    cnt,gal,save=week
    total_gal,total_save=total
    await c.bot.send_message(chat_id=u.effective_chat.id,
        text=f"📊 ОТЧЕТ Пт-Пт {last_fri.strftime('%m/%d')} - {next_fri.strftime('%m/%d')} CT\nВсего: ${total_save:.2f} | {total_gal:.1f} gal\nС {last_fri.strftime('%m/%d')}: {cnt} зап • {gal:.1f} gal • ${save:.2f}")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v33 FAST",flush=True)
    app.run_polling()
