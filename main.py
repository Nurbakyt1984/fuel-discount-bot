import os, re, base64, time, datetime, sqlite3, json, traceback
from openai import OpenAI
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
DB = "fuel.db"

def init_db():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER, date TEXT, gallons REAL, disc REAL, saving REAL, loc TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, chat_id INTEGER)")
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
    r=cur.fetchone(); con.close()
    return (r[0], r[1] or 0, r[2] or 0) if r and r[0] else (0,0,0)

async def ask_gpt(b):
    if not client: return {"error":"NO_KEY"}
    b64=base64.b64encode(b).decode()
    prompt='''You are fuel OCR. IGNORE DEF completely. Return ONLY JSON:
{"pump_price":5.359, "app_price":4.80, "gallons":69.74, "diesel_gallons":69.74, "diesel_price":5.179, "location":"TA Grand Island"}
Rules: Never return DEF, 2.916, 4.899. Only DIESEL line. Fix 36 1.18->361.18. Only JSON.'''
    try:
        r=client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}], max_tokens=150)
        txt=r.choices[0].message.content.strip()
        m=re.search(r'\{.*\}', txt, re.DOTALL)
        if m: txt=m.group(0)
        print(f"GPT: {txt}")
        return json.loads(txt)
    except Exception as e:
        print(f"GPT ERR {e}")
        return {"error":str(e)}

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit(); con.close()
    user_data[u.effective_user.id]={'prices':[], 'gals':[], 'loc':"", 't':0}
    await u.message.reply_text("v9.1 без DEF готов! Кидай чек - DEF игнорирую")

async def handle(uid, data, upd):
    if "error" in data:
        await upd.message.reply_text(f"OpenAI ошибка: {data['error'][:400]}")
        return
    now=time.time()
    if uid not in user_data or now-user_data[uid]['t']>300:
        user_data[uid]={'prices':[], 'gals':[], 'loc':"", 't':now}
    user_data[uid]['t']=now

    # Добавляем только DIESEL, DEF
