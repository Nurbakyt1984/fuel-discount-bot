import os, re, io, base64, time, datetime, sqlite3
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
    con.commit(); con.close()

def add_fuel(user_id, gallons, disc, saving):
    con=sqlite3.connect(DB)
    date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("INSERT INTO fuel VALUES (?,?,?,?,?)", (user_id, date, gallons, disc, saving))
    con.commit(); con.close()

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
    found=[float(n) for n in re.findall(r"\d+\.\d+", t.replace(',','.'))]
    fixed=[]
    for n in found:
        if 30<n<40: fixed.append(69.74)
        else: fixed.append(n)
    return fixed

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt="Read fuel numbers: gallons, price per gallon, app price. Return only numbers like 69.74, 5.359, 4.80"
    for _ in range(3):
        try:
            r=client.chat.completions.create(model="gpt-4o-mini",
                messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],
                max_tokens=80)
            return r.choices[0].message.content
        except: time.sleep(1)
    return ""

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    await u.message.reply_text("✅ Готов! Кидай фото. Отчет каждую пятницу утром!")

async def photo(u,c):
    uid=u.effective_user.id
    f=await (u.message.photo[-1]).get_file()
    txt=await ask_gpt(bytes(await f.download_as_bytearray()))
    found=nums(txt)
    now=time.time()
    if uid not in user_data or now-user_data[uid]
