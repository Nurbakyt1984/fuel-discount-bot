import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import openpyxl, os, re

BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН"
DB = "fuel.db"

def init():
    c = sqlite3.connect(DB)
    c.execute('CREATE TABLE IF NOT EXISTS f(date TEXT, pump REAL, disc REAL, gal REAL, total REAL, saved REAL)')
    c.commit(); c.close()

async def start(u,c):
    await u.message.reply_text("Кидай фото чека и пиши так: 5.439 4.80 69.74\nЯ сам посчитаю!\n/report - отчет за неделю")

async def handle_text(u,c):
    txt = u.message.text
    nums = [float(x) for x in re.findall(r"\d+\.\d+", txt)]
    if len(nums) < 3:
        await u.message.reply_text("Нужно 3 цифры: цена колонки, цена скидки, галлоны\nПример: 5.439 4.80 69.74")
        return
    pump, disc, gal = nums[0], nums[1], nums[2]
    total = disc*gal
    saved = (pump-disc)*gal
    con = sqlite3.connect(DB); con.execute("INSERT INTO f VALUES (?,?,?,?,?,?)",(datetime.now().isoformat(),pump,disc,gal,total,saved)); con.commit(); con.close()
    await u.message.reply_text(f"✅ Сохранено!\nГаллоны: {gal}\nСэкономил: ${saved:.2f}")

async def report(u,c):
    today = datetime.now()
    last_f = today - timedelta(days=(today.weekday()-4)%7+7)
    con = sqlite3.connect(DB); rows = con.execute("SELECT * FROM f WHERE date>=?",(last_f.isoformat(),)).fetchall(); con.close()
    if not rows: await u.message.reply_text("Нет данных"); return
    tg = sum(r[3] for r in rows); ts = sum(r[5] for r in rows)
    wb=openpyxl.Workbook(); ws=wb.active; ws.append(["Дата","Колонка","Скидка","Галлоны","Сэкономлено"])
    for r in rows: ws.append([r[0][:10],r[1],r[2],r[3],r[5]])
    ws.append([]); ws.append(["ИТОГО","","",tg,ts])
    fn="otchet.xlsx"; wb.save(fn)
    await u.message.reply_document(open(fn,'rb'), caption=f"Отчет {last_f.strftime('%d.%m')} - {today.strftime('%d.%m')}\nСэкономлено: ${ts:.2f}")

app = Application.builder().token(BOT_TOKEN).build()
init()
app.add_handler(CommandHandler("start",start))
app.add_handler(CommandHandler("report",report))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.run_polling()
