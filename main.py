async def ask_gpt(b):
    # СЖИМАЕМ в 4 раза быстрее
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(b))
    img.thumbnail((1024,1024))
    buf=io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64=base64.b64encode(buf.getvalue()).decode()
    prompt='ONLY JSON: {"pump_price":5.439,"gallons":120.553,"app_price":4.94}'
    r=await asyncio.to_thread(lambda: client.chat.completions.create(model="gpt-4o-mini",
        messages=[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]}],
        max_tokens=60))
    txt=r.choices[0].message.content.strip()
    m=re.search(r'\{.*\}',txt,re.DOTALL)
    if m: txt=m.group(0)
    try: return json.loads(txt)
    except: return {}

async def album_job(context):
    data=context.job.data
    files=data['files']
    # ПАРАЛЛЕЛЬНО, а не по очереди!
    results = await asyncio.gather(*[ask_gpt(b) for b in files])
    #... дальше как было
