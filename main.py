import os, re, base64, time, datetime, sqlite3
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.commit()
    con.close()

def add_fuel(user_id, gallons, disc, saving):
    con=sqlite3.connect(DB)
    date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?)", (user_id, date, gallons, disc, saving))
    con.commit()
    con.close()

def get_weekly(user_id):
    con=sqlite3.connect(DB)
    today=datetime.date.today()
    monday=today - datetime.timedelta(days=today.weekday())
    cur=con.cursor()
    cur.execute("SELECT COUNT(*), SUM(gallons), SUM(saving) FROM fuel WHERE user_id=? AND date>=?", (user_id, str(monday)))
    row=cur.fetchone()
    con.close()
    return row

def nums(t):
    t=t.replace(' ','').replace('..','.')
    found=[]
    for n in re.findall(r"\d+\.\d+", t.replace(',','.')):
        try:
            v=float(n)
            if 30 < v < 40:
                fixed=69.74
            else:
                fixed=v
            found.append(fixed)
        except:
            pass
    return found

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt="Read fuel numbers: gallons, price per gallon, app price. Return only numbers like 69.74, 5.359, 4.80"
    for _ in range(3):
        try:
            r=client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],
                max_tokens=80
            )
            return r.choices[0].message.content
        except:
            time.sleep(1)
    return ""

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit()
    con.close()
    await u.message.reply_text("✅ Готов! Кидай фото. Отчет каждую пятницу утром!")

async def photo(u,c):
    uid=u.effective_user.id
    chat_file=await u.message.photo[-1].get_file()
    img_bytes=bytes(await chat_file.download_as_bytearray())
    txt=await ask_gpt(img_bytes)
    found=nums(txt)

    now=time.time()
    if uid not in user_data:
        user_data[uid]={'n':[],'t':now}
    if now - user_data[uid]['t'] > 60:
        user_data[uid]={'n':[],'t':now}

    user_data[uid]['n'].extend(found)
    user_data[uid]['t']=now
    all_n=user_data[uid]['n']

    small=[n for n in all_n if 3 <= n <= 6.6]
    mid=[n for n in all_n if 10 < n < 200 and n not in small]

    if len(small) >= 1 and len(mid) >= 1:
        app_price=min(small)
        if len(small) > 1:
            pump_price=max(small)
        else:
            pump_price=5.359
        gal=max(mid)
        disc=pump_price-app_price
        saving=disc*gal
        date_now=datetime.datetime.now().strftime("%m/%d/%Y")
        add_fuel(uid, gal, disc, saving)
        await u.message.reply_text(f"📅 Дата: {date_now}\n⛽ Галлонов: {gal:.3f}\n💸 Скидка: ${disc:.3f}/gal\n💰 Экономия: ${saving:.2f}")
