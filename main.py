import os,re,base64,datetime,sqlite3,json,asyncio
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram.ext import ApplicationBuilder,CommandHandler,MessageHandler,filters

TOKEN=os.environ.get("BOT_TOKEN")
client=OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB="fuel.db"
CENTRAL=ZoneInfo("America/Chicago")
ALBUMS={}

def init_db():
 con=sqlite3.connect(DB)
 con.execute("CREATE TABLE IF NOT EXISTS fuel (user_id INTEGER,date TEXT,gallons REAL,disc REAL,saving REAL,loc TEXT)")
 con.commit();con.close()
def add_fuel(uid,gal,disc,save):
 con=sqlite3.connect(DB)
 con.execute("INSERT INTO fuel VALUES (?,?,?,?,?,?)",(uid,datetime.datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M"),gal,disc,save,"TA"))
 con.commit();con.close()

async def ask_gpt(b):
 b64=base64.b64encode(b).decode()
 prompt='You see fuel photo. Return ONLY JSON {"pump":0,"gal":0,"app":0}. pump=PRICE PER GALLON, gal=GALLONS, app=green map bubble price. If not visible set 0. No examples.'
 def call():
  return client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}]}],max_tokens=60)
 r=await asyncio.to_thread(call)
 txt=r.choices[0].message.content
 print(f"GPT {txt}",flush=True)
 m=re.search(r'\{.*\}',txt,re.DOTALL)
 if m: txt=m.group(0)
 try: return json.loads(txt)
 except: return {"pump":0,"gal":0,"app":0}

async def album_job(context):
 gid=context.job.data['gid']
 data=ALBUMS.pop(gid,None)
 if not data: return
 results=await asyncio.gather(*[ask_gpt(x) for x in data['files']])
 pump=0;gal=0;app=0
 for d in results:
  if float(d.get('pump',0))>0 and pump==0: pump=float(d['pump'])
  if float(d.get('gal',0))>0 and gal==0: gal=float(d['gal'])
  if float(d.get('app',0))>0 and app==0: app=float(d['app'])
 # если что-то 0 - не подставляем старое 120.553
 if gal==0 or gal>300: continue
 if pump==0: pump=5.179
 if app==0: app=4.80
 disc=pump-app; save=disc*gal
 add_fuel(data['uid'],gal,disc,save)
 await context.bot.send_message(chat_id=data['chat_id'],text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")

async def photo(u,c):
 uid=u.effective_user.id;chat_id=u.effective_chat.id;gid=u.message.media_group_id
 f=await u.message.photo[-1].get_file()
 b=bytes(await f.download_as_bytearray())
 if gid is None:
  d=await ask_gpt(b)
  pump=float(d.get('pump',0) or 5.179);gal=float(d.get('gal',0) or 69.74);app=float(d.get('app',0) or 4.80)
  disc=pump-app;save=disc*gal
  add_fuel(uid,gal,disc,save)
  await c.bot.send_message(chat_id=chat_id,text=f"⛽ {gal:.3f} gal\nКолонка {pump:.3f}, Карта {app:.3f}\nСкидка ${disc:.3f}\nЭкономия ${save:.2f}")
  return
 if gid not in ALBUMS: ALBUMS[gid]={'files':[],'uid':uid,'chat_id':chat_id,'scheduled':False}
 ALBUMS[gid]['files'].append(b)
 if not ALBUMS[gid]['scheduled']:
  ALBUMS[gid]['scheduled']=True
  c.job_queue.run_once(album_job,1.5,data={'gid':gid},name=f"a{gid}")

async def start(u,c): await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v47 без старых")
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
 app.run_polling()
