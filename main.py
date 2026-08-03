import os, re, base64, datetime, sqlite3, json, asyncio
from zoneinfo import ZoneInfo
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

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='ONLY JSON. From this image: pump_price (like 5.439), gallons (like 120.553), app_price (map $4.94). Return {"pump_price":5.439,"gallons":120.553,"app_price":4.94}. Only JSON.'
    def call():
        return client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}
            ]}], max_tokens=80)
    try:
        r=await asyncio.to_thread(call)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}',txt,re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT OK: {txt}",flush=True)
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERROR: {e}",flush=True)
        return {}

async def album_job(context):
    data=context.job.data
    uid=data['uid']; chat_id=data['chat_id']; files=data['files']
    try:
        results=await asyncio.gather(*[ask_gpt(b) for b in files])
        print(f"RESULTS {results}",flush=True)
        pump=app=gal=None
        for d in results:
            try:
                if d.get('pump_price'):
                    v=float(str(d['pump_price']).replace(',','.'))
                    if 2.5<=v<=7.5: pump=v
                if d.get('app_price'):
                    v=float(str(d['app_price']).replace(',','.'))
                    if 2.5<=v<=7.5: app=v
                for k in ['gallons','diesel_gallons','pump_gallons']:
                    if d.get(k) is not None:
                        v=float(str(d[k]).replace(',','.'))
                        if 1<=v<=400: gal=v
            except: pass

        if pump is None or app is None or gal is None:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Не прочитал: {results}\nПопробуй переснять ближе")
            return

        disc=pump-app; save=disc*gal
        add_fuel(uid,gal,disc,save)
        await context.bot.send_message(chat_id=chat_id,
            text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL (без DEF)\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal ({pump:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")
    except Exception as e:
        print(f"JOB ERROR: {e}",flush=True)
        await context.bot.send_message(chat_id=chat_id, text=f"Ошибка: {e}")

async def photo(u,c):
    try:
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
    except Exception as e:
        print(f"PHOTO ERROR: {e}",flush=True)

async def start(u,c):
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v34 STABLE готов! Кидай коллаж")
async def clear_cmd(u,c):
    try:
        for jobs in c.job_queue.jobs():
            for j in jobs[1]: j.schedule_removal()
    except: pass
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")
async def report_cmd(u,c):
    week,total,last_fri,next_fri=get_weekly(u.effective_user.id)
    cnt,gal,save=week
    total_gal,total_save=total
    await c.bot.send_message(chat_id=u.effective_chat.id,
        text=f"📊 Пт-Пт {last_fri.strftime('%m/%d')} - {next_fri.strftime('%m/%d')} CT\nВсего: ${total_save:.2f} | {total_gal:.1f} gal\nС {last_fri.strftime('%m/%d')}: {cnt} зап • {gal:.1f} gal • ${save:.2f}")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v34 STABLE",flush=True)
    app.run_polling()
