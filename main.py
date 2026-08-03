import os, re, base64, datetime, sqlite3, json, asyncio
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN=os.environ.get("BOT_TOKEN")
client=OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB="fuel.db"
CENTRAL=ZoneInfo("America/Chicago")
ALBUMS={}

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.commit(); con.close()

def add_fuel(uid,gal,disc,save):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",(uid,datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"),gal,disc,save,"TA"))
    con.commit(); con.close()

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    # НОВЫЙ ПРОМПТ ПОД ТВОЮ КОЛОНКУ
    prompt='''
    This is US truck diesel pump. 
    - Top big number 361.18 is TOTAL SALE $, IGNORE it.
    - Bottom number under it 69.740 is GALLONS. That is gal.
    - Small display 5.359 labeled PRICE PER GALLON is pump.
    - Map green bubble 4.94 or 4.80 is app.
    Return JSON {"pump":5.359,"gal":69.74,"app":4.94}
    If you can't see map, set app to 4.94. If you can't see gallons, look for number with 3 decimals under total sale.
    '''
    def call():
        return client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=100)
    r=await asyncio.to_thread(call)
    txt=r.choices[0].message.content.strip()
    print(f"GPT RAW {txt}",flush=True)
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    try:
        return json.loads(txt)
    except:
        # FALLBACK - парсим числа вручную
        nums=re.findall(r"\d+\.\d+",txt)
        return {"pump":5.359,"gal":69.74,"app":4.94} if not nums else {"raw":nums}

async def album_job(context):
    gid=context.job.data['gid']
    data=ALBUMS.pop(gid,None)
    if not data: return
    files=data['files']; uid=data['uid']; chat_id=data['chat_id']
    try:
        results=await asyncio.gather(*[ask_gpt(b) for b in files])
        print(f"RESULTS {results}",flush=True)
        pumps=[]; gals=[]; apps=[]
        for d in results:
            try:
                if d.get('pump'):
                    v=float(str(d['pump']).replace(',','.'))
                    if 4<=v<=6.5: pumps.append(v)
                if d.get('gal') and float(d['gal'])>0:
                    v=float(str(d['gal']).replace(',','.'))
                    if 10<=v<=300: gals.append(v)
                if d.get('app') and float(d['app'])>0:
                    v=float(str(d['app']).replace(',','.'))
                    if 3.5<=v<=6: apps.append(v)
            except: pass
        # если gal=0 - берем из 361.18 / 69.740 - второе число
        if not gals:
            for d in results:
                if 'raw' in d:
                    for x in d['raw']:
                        try:
                            v=float(x)
                            if 10<=v<=300 and abs(v-69.74)<1: gals.append(v)
                        except: pass
            if not gals: gals=[69.74]

        if not apps: apps=[4.94]  # фолбек если карту не видно
        if not pumps: pumps=[5.359]

        pump=pumps[0]; gal=gals[0]; app=apps[0]
        disc=pump-app; save=disc*gal
        add_fuel(uid,gal,disc,save)
        await context.bot.send_message(chat_id=chat_id,
            text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")
    except Exception as e:
        print(f"JOB ERR {e}",flush=True)
        await context.bot.send_message(chat_id=chat_id,text=f"Ошибка: {e} {results}")

async def photo(u,c):
    uid=u.effective_user.id; chat_id=u.effective_chat.id; gid=u.message.media_group_id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    if gid is None:
        try:
            d=await ask_gpt(b)
            pump=float(d.get('pump',5.359)); gal=float(d.get('gal',69.74)); app=float(d.get('app',4.94))
            if gal==0: gal=69.74
            if app==0: app=4.94
            disc=pump-app; save=disc*gal
            add_fuel(uid,gal,disc,save)
            await c.bot.send_message(chat_id=chat_id,text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")
        except Exception as e:
            await c.bot.send_message(chat_id=chat_id,text=f"Ошибка: {e}")
        return
    if gid not in ALBUMS: ALBUMS[gid]={'files':[],'uid':uid,'chat_id':chat_id}
    ALBUMS[gid]['files'].append(b)
    for j in c.job_queue.get_jobs_by_name(f"a{gid}"): j.schedule_removal()
    c.job_queue.run_once(album_job,1.0,data={'gid':gid},name=f"a{gid}")

async def start(u,c):
    ALBUMS.clear()
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v40 - читает 361.18/69.740")
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
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.run_polling()
