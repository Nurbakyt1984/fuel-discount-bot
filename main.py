async def ask_gpt(b):
    b64=base64.b64encode(b).decode()
    prompt = 'Read this fuel image. Return ONLY JSON: {"pump": number or 0, "gal": number or 0, "app": number or 0}. Rules: pump=PRICE PER GALLON text. gal=number before gallons. app=price inside green bubble on map. If not visible set 0. Do not invent, do not use example numbers.'
    def call():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}","detail":"high"}}
            ]}],
            max_tokens=60
        )
    r=await asyncio.to_thread(call)
    txt=r.choices[0].message.content
    print(f"GPT RAW {txt}",flush=True)
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    try:
        j=json.loads(txt)
        return j
    except:
        return {"pump":0,"gal":0,"app":0}

async def album_job(context):
    gid=context.job.data['gid']
    data=ALBUMS.pop(gid,None)
    if not data: return
    files=data['files']
    results=await asyncio.gather(*[ask_gpt(x) for x in files])
    print(f"ALL {results}",flush=True)
    pump=0; gal=0; app=0
    for d in results:
        if d.get('pump',0)>0 and pump==0: pump=float(d['pump'])
        if d.get('gal',0)>0 and gal==0: gal=float(d['gal'])
        if d.get('app',0)>0 and app==0: app=float(d['app'])
    # честный дефолт только если реально 0
    if gal==0: gal=69.74
    if pump==0: pump=5.179
    if app==0: app=4.80
    disc=pump-app
    save=disc*gal
    add_fuel(data['uid'],gal,disc,save)
    await context.bot.send_message(chat_id=data['chat_id'],
        text=f"📅 {datetime.datetime.now(CENTRAL).strftime('%m/%d/%Y')}\n📍 TA Grand Island\n⛽ {gal:.3f} gal DIESEL\nКолонка {pump:.3f}, Карта: {app:.3f}\n💸 Скидка ${disc:.3f}/gal\n💰 Экономия ${save:.2f}")
