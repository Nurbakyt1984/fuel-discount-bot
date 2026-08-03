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

def add_fuel(uid,gal,disc,save):
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",(uid,datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"),gal,disc,save,"TA"))
    con.commit(); con.close()

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt='ONLY this image. Get pump price 4-6 like 5.439, gallons like 120.553 or 69.740, app map price like 4.94. IGNORE sale $648.45. Return JSON {"pump":5.439,"gal":120.553,"app":4.94}'
    def call():
        return client.chat.completions.create(model="gpt-4o-mini",
            messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=80)
    r=await asyncio.to_thread(call)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    return json.loads(txt)

async def photo(u,c):
    # МГНОВЕННО, БЕЗ БУФЕРА
    try:
        f=await u.message.photo[-1].get_file()
        b=bytes(await f.download_as_bytearray())
        d=await ask_gpt(b)
        print(f"GOT {d}",flush=True)

        pump=float(str(d.get('pump')).replace(',','.'))
        gal=float(str(d.get('gal')).replace(',','.'))
        app=float(str(d.get('app')).replace(',','.'))

        if not (4.0<=pump<=6.5 and 10<=gal<=300 and 3.5<=app<=6.0):
            await c.bot.send_message(chat_id=u.effective_chat.id,text=f"❌ Бред: {d}")
            return

        disc=pump-app; save=disc*gal
        add_fuel(u.effective_user.id,gal,disc,save)
        await c.bot.send_message(chat_id=u.effective_chat.id,
            text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")
    except Exception as e:
        await c.bot.send_message(chat_id=u.effective_chat.id,text=f"Ошибка: {e}")

async def start(u,c): await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v38 - без памяти, мгновенно")
async def clear_cmd(u,c): await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ Очищено")

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("clear",clear_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.run_polling()
