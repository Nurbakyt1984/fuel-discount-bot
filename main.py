import os
import re
import base64
import datetime
import sqlite3
import json
import asyncio
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB = "fuel.db"
CENTRAL = ZoneInfo("America/Chicago")

ALBUMS = {}

def init_db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER,date TEXT,gallons REAL,disc REAL,saving REAL,loc TEXT)")
    con.commit()
    con.close()

def add_fuel(uid, gal, disc, save):
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",(uid,datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"),gal,disc,save,"TA Grand Island"))
    con.commit()
    con.close()

async def ask_gpt(b):
    b64 = base64.b64encode(b).decode()
    prompt = 'Return ONLY JSON {"pump":0,"gal":0,"app":0}. pump=PRICE PER GALLON, gal=GALLONS, app=map bubble price. If not visible set 0.'
    def call():
        return client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=60)
    r = await asyncio.to_thread(call)
    txt = r.choices[0].message.content
    m = re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt = m.group(0)
    try: return json.loads(txt)
    except: return {"pump":0,"gal":0,"app":0}

async def album_job(context):
    gid = context.job.data['gid']
    data = ALBUMS.pop(gid, None)
    if not data: return
    results = await asyncio.gather(*[ask_gpt(x) for x in data['files']])
    pump=0; gal=0; app=0
    for d in results:
        try:
            p=float(str(d.get('pump',0)).replace(',','.'))
            g=float(str(d.get('gal',0)).replace(',','.'))
            a=float(str(d.get('app',0)).replace(',','.'))
            if 4 <= p <= 6.5 and pump==0: pump=p
            if 10 <= g <= 300 and gal==0: gal=g
            if 3.5 <= a <= 6 and app==0: app=a
        except: pass
    if gal==0:
        return
    if pump==0: pump=5.439
    if app==0: app=4.94
    disc=pump-app
    save=disc*gal
    add_fuel(data['uid'],gal,disc,save)
    await context.bot.send_message(chat_id=data['chat_id'],text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")

async def photo(u,c):
    uid=u.effective_user.id; chat_id=u.effective_chat.id; gid=u.message.media_group_id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    if gid is None:
        d=await ask_gpt(b)
        try:
            pump=float(str(d.get('pump',0)).replace(',','.')) or 5.439
            gal=float(str(d.get('gal',0)).replace(',','.')) or 0
            app=float(str(d.get('app',0)).replace(',','.')) or 4.94
        except: pump=5.439; gal=0; app=4.94
        if gal==0:
            await c.bot.send_message(chat_id=chat_id,text="Не вижу галлоны")
            return
        disc=pump-app; save=disc*gal
        add_fuel(uid,gal,disc,save)
        await c.bot.send_message(chat_id=chat_id,text=f"⛽ {gal:.3f} gal\nКолонка {pump:.3f}, Карта {app:.3f}\nСкидка ${disc:.3f}\nЭкономия ${save:.2f}")
        return
    if gid not in ALBUMS: ALBUMS[gid]={'files':[],'uid':uid,'chat_id':chat_id,'scheduled':False}
    ALBUMS[gid]['files'].append(b)
    if not ALBUMS[gid]['scheduled']:
        ALBUMS[gid]['scheduled']=True
        c.job_queue.run_once(album_job,1.5,data={'gid':gid},name=f"a{gid}")

async def start(u,c): await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v48 работает")
async def clear_cmd(u,c):
    ALBUMS.clear()
    for jobs in c.job_queue.jobs():
        for j in jobs[1]: j.schedule_removal()
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v48 START",flush=True)
    app.run_polling()
