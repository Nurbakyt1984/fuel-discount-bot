import os, re, base64, datetime, sqlite3, json
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
    prompt='ONLY JSON. Read this image only, no memory. If receipt: diesel_gallons at 5.179 is 69.74, pump_price is 5.179, app_price is 4.80. If pump LED 5.439 + bottom 120.553 + map 4.94. Return {"pump_price":5.179,"diesel_gallons":69.74,"app_price":4.80}. Only JSON.'
    r=client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=100)
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    print(f"GPT:{txt}",flush=True)
    try: return json.loads(txt)
    except: return {}

async def album_job(context):
    data=context.job.data
    uid=data['uid']; chat_id=data['chat_id']; files=data['files']; gid=data['gid']
    print(f"JOB {gid} files={len(files)}",flush=True)
    results=[]
    for b in files:
        results.append(await ask_gpt(b))

    pump=diesel=app=gal=None
    has_pump=has_receipt=False
    for d in results:
        if "pump_price" in d:
            try:
                v=float(str(d["pump_price"]).replace(',','.'))
                if 2.
