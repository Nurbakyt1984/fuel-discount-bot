import os, re, base64, datetime, sqlite3, json, asyncio, time
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)
DB = "fuel.db"
CENTRAL = ZoneInfo("America/Chicago")

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.commit(); con.close()

def add_fuel(uid, gal, disc, save, loc=""):
    con=sqlite3.connect(DB)
    now_str = datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)", (uid, now_str, gal, disc, save, loc))
    con.commit(); con.close()

def get_weekly(uid):
    con=sqlite3.connect(DB)
    today = datetime.datetime.now(CENTRAL).date()
    days_since_friday = (today.weekday() - 4) % 7
    last_friday = today - datetime.timedelta(days=days_since_friday)
    next_friday = last_friday + datetime.timedelta(days=7)
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=? AND date<?",
                (uid, str(last_friday), str(next_friday)))
    r=cur.fetchone()
    cur.execute("SELECT SUM(gallons), SUM(saving) FROM fuel WHERE user_id=?", (uid,))
    r2=cur.fetchone()
    con.close()
    week = (r[0], r[1] or 0, r[2] or 0) if r and r[0] else (0,0,0)
    total = (r2[0] or 0, r2[1] or 0) if r2 else (0,0)
    return week, total, last_friday, next_friday

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='ONLY JSON, read ONLY this image: pump_price LED 5.439, diesel_price receipt at 5.179, diesel_gallons 69.74, pump_gallons bottom 120.553, app_price map bubble $4.80 or $4.94. Only JSON.'
    r=client.chat.completions.create(model="gpt-4o-mini",
        messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],
        max_tokens=100)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}', txt, re.DOTALL)
    if m: txt=m.group(0)
    print(f"GPT: {txt}")
    try: return json.loads(txt)
    except: return {}

user_cache={}

async def try_calc(uid, chat_id, context):
    data=user_cache.get(uid)
    if not data: return
    if time.time()-data['ts']>12:
        del user_cache[uid]; return
    pump=data.get('pump'); diesel=data.get('diesel'); app=data.get('app'); gal=data.get('gal')
    has_pump=data.get('has_pump',False); has_receipt=data.get('has_receipt',False)
    if gal is None or app is None: return
    if not has_pump and not has_receipt: return
    if has_pump and pump is not None:
        base=pump; label=f"Колонка {pump:.3f}"
    elif has_receipt and diesel is not None:
        base=diesel; label=f"Чек {diesel:.3f}"
    else: return
    disc=base-app; save=disc*gal
    add_fuel(uid,gal,disc,save,"TA Grand Island")
    await context.bot.send_message(chat_id=chat_id,
        text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL (без DEF)\n{label}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal ({base:.3f} - {app:.3f})\n💰 Экономия ${save:.2f}")
    del user_cache[uid]

async def photo(u,c):
    uid=u.effective_user.id
    f=await u.message.photo[-1].get_file()
    b=bytes(await f.download_as_bytearray())
    d=await ask_gpt(b)
    print(f"PHOTO {uid} -> {d}")
    now=time.time()
    if uid not in user_cache or now - user_cache[uid]['ts'] > 12:
        user_cache[uid]={'ts':now}
    cache=user_cache[uid]
    cache['ts']=now
    if "pump_price" in d:
        try:
            v=float(str(d["pump_price"]).replace(',','.'))
            if 2.5<=v<=7.5: cache['pump']=v; cache['has_pump']=True
        except: pass
    if "diesel_price" in d:
        try:
            v=float(str(d["diesel_price"]).replace(',','.'))
            if 2.5<=v<=7.5: cache['diesel']=v; cache['has_receipt']=True
        except: pass
    if "app_price" in d:
        try:
            v=float(str(d["app_price"]).replace(',','.'))
            if 2.5<=v<=7.5: cache['app']=v
        except: pass
    for k in ["diesel_gallons","pump_gallons","gallons"]:
        if k in d:
            try:
                v=float(str(d[k]).replace(',','.'))
                if 1<=v<=200: cache['gal']=v
            except: pass
    await try_calc(uid,u.effective_chat.id,c)

async def start(u,c):
    user_cache.pop(u.effective_user.id,None)
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v27 готов! Пт-Пт по Central")
async def clear_cmd(u,c):
    user_cache.pop(u.effective_user.id,None)
    await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")
async def report_cmd(u,c):
    (cnt,gal,save),(total_gal,total_save),last_fri,next_fri = get_weekly(u.effective_user.id)
    text=f"📊 ОТЧЕТ | Пт-Пт {last_fri.strftime('%m/%d')} - {next_fri.strftime('%m/%d')} CT\nПолучено скидок: ${total_save:.2f} | Заправлено: {total_gal:.1f} галлон\n\nС {last_fri.strftime('%m/%d')} по сегодня: {cnt} зап • {gal:.1f} gal • ${save:.2f}\nВсего: {total_gal:.1f} gal • ${total_save:.2f}"
    await c.bot.send_message(chat_id=u.effective_chat.id,text=text)

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(CommandHandler("Clear",clear_cmd))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    print("v27 FINAL CT FRIDAY-FRIDAY")
    app.run_polling()
