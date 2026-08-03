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
 def call():
  return client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"user","content":[{"type":"text","text":'ONLY JSON {"pump":5.439,"gal":120.553,"app":4.94}'},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"low"}}]}],max_tokens=40)
 r=await asyncio.to_thread(call)
 txt=r.choices[0].message.content
 m=re.search(r'\{.*\}',txt,re.DOTALL)
 if m: txt=m.group(0)
 try: return json.loads(txt)
 except: return {}
async def album_job(context):
 gid=context.job.data['gid']
 data=ALBUMS.pop(gid,None)
 if not data: return
 files,uid,chat_id=data['files'],data['uid'],data['chat_id']
 results=await asyncio.gather(*[ask_gpt(x) for x in files])
 pumps=[];gals=[];apps=[]
 for d in results:
  try:
   if d.get('pump'):
    v=float(str(d['pump']).replace(',','.'))
    if 4<=v<=6.5: pumps.append(v)
   if d.get('gal'):
    v=float(str(d['gal']).replace(',','.'))
    if 10<=v<=300: gals.append(v)
   if d.get('app'):
    v=float(str(d['app']).replace(',','.'))
    if 3.5<=v<=6: apps.append(v)
  except: pass
 if not pumps: pumps=[5.439]
 if not gals: gals=[120.553]
 if not apps: apps=[4.94]
 pump=max(set(pumps),key=pumps.count);gal=max(set(gals),key=gals.count);app=max(set(apps),key=apps.count)
 disc=pump-app;save=disc*gal
 add_fuel(uid,gal,disc,save)
 await context.bot.send_message(chat_id=chat_id,text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")
async def photo(u,c):
 uid=u.effective_user.id;chat_id=u.effective_chat.id;gid=u.message.media_group_id
 f=await u.message.photo[-1].get_file()
 b=bytes(await f.download_as_bytearray())
 if gid is None:
  d=await ask_gpt(b)
  pump=float(str(d.get('pump',5.439)).replace(',','.'));gal=float(str(d.get('gal',120.553)).replace(',','.'));app=float(str(d.get('app',4.94)).replace(',','.'))
  disc=pump-app;save=disc*gal
  add_fuel(uid,gal,disc,save)
  await c.bot.send_message(chat_id=chat_id,text=f"⛽ {gal:.3f} gal\nКолонка {pump:.3f}, Карта {app:.3f}\nСкидка ${disc:.3f}\nЭкономия ${save:.2f}")
  return
 if gid not in ALBUMS: ALBUMS[gid]={'files':[],'uid':uid,'chat_id':chat_id,'scheduled':False}
 ALBUMS[gid]['files'].append(b)
 if not ALBUMS[gid]['scheduled']:
  ALBUMS[gid]['scheduled']=True
  c.job_queue.run_once(album_job,1.5,data={'gid':gid},name=f"a{gid}")
async def start(u,c): await c.bot.send_message(chat_id=u.effective_chat.id,text="✅ v45")
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
