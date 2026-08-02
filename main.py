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
    if row[0] is None: return (0,0,0)
    return (row[0], row[1] or 0, row[2] or 0)

def nums(t):
    t=t.replace(' ','').replace('..','.')
    found=[]
    for n in re.findall(r"\d+\.\d+", t.replace(',','.')):
        try:
            v=float(n)
            # Фикс 36.118 -> 361.18
            if 30 < v < 40:
                v = v*10
            found.append(v)
        except:
            pass
    return found

async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt="Read fuel pump: This Sale, Gallons, Price per gallon. Return numbers only like 361.18, 69.74, 5.359"
    for _ in range(3):
        try:
            r=client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],
                max_tokens=80
            )
            return r.choices[0].message.content
        except Exception as e:
            print(f"GPT error: {e}")
            time.sleep(1)
    return ""

user_data={}

async def start(u,c):
    con=sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users VALUES (?,?)", (u.effective_user.id, u.effective_chat.id))
    con.commit()
    con.close()
    await u.message.reply_text("✅ Готов! Кидай фото.")

async def photo(u,c):
    uid=u.effective_user.id
    print(f"Photo from {uid}")
    chat_file=await u.message.photo[-1].get_file()
    img_bytes=bytes(await chat_file.download_as_bytearray())
    txt=await ask_gpt(img_bytes)
    print(f"GPT raw: {txt}")
    found=nums(txt)
    print(f"Found: {found}")

    now=time.time()
    if uid not in user_data:
        user_data[uid]={'n':[],'t':now}
    if now - user_data[uid]['t'] > 90:
        user_data[uid]={'n':[],'t':now}

    user_data[uid]['n'].extend(found)
    user_data[uid]['t']=now
    all_n=user_data[uid]['n']
    print(f"All: {all_n}")

    small=[n for n in all_n if 3 <= n <= 6.6]
    mid=[n for n in all_n if 10 < n < 200 and n not in small]
    large=[n for n in all_n if n >= 200]

    if len(small) >= 1 and len(mid) >= 1:
        app_price=min(small)
        pump_price=max(small) if len(small)>1 else 5.359
        gal=max(mid)
        # Если есть Sale, используем его для точности
        if large:
            sale = max(large)
            # Пересчитаем pump из Sale/Gal если small только 1
            if len(small)==1 and gal>0:
                pump_price = sale / gal

        disc=pump_price-app_price
        saving=disc*gal
        date_now=datetime.datetime.now().strftime("%m/%d/%Y")
        add_fuel(uid, gal, disc, saving)
        await u.message.reply_text(f"📅 Дата: {date_now}\n⛽ Галлонов: {gal:.3f}\n💸 Скидка: ${disc:.3f}/gal\n💰 Экономия: ${saving:.2f}")
        user_data[uid]={'n':[],'t':0}
    else:
        await u.message.reply_text(f"Принял {found}, кидай еще 1 фото... Собрал: {all_n}")

async def report_cmd(u,c):
    count, gallons, saving = get_weekly(u.effective_user.id)
    if count==0:
        await u.message.reply_text("Заправок на этой неделе нет")
    else:
        await u.message.reply_text(f"📊 Отчет с понедельника:\n⛽ Заправок: {count}\n🛢️ Галлонов: {gallons:.1f}\n💰 Экономия: ${saving:.2f}")

async def weekly_job(context):
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT user_id, chat_id FROM users")
    for user_id, chat_id in cur.fetchall():
        count, gallons, saving = get_weekly(user_id)
        if count>0:
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"📊 ОТЧЕТ ПЯТНИЦА:\n⛽ Заправок: {count}\n🛢️ Всего галлонов: {gallons:.1f}\n💰 Всего скидка: ${saving:.2f}")
            except:
                pass
    con.close()

if __name__=="__main__":
    init_db()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("report",report_cmd))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    # Пятница 8:00 утра Central = 13:00 UTC
    try:
        app.job_queue.run_daily(weekly_job, time=datetime.time(hour=13, minute=0, second=0), days=(4,))
        print("Job queue OK")
    except Exception as e:
        print(f"Job queue error: {e}")
    print("v6.2 fixed started")
    app.run_polling()
