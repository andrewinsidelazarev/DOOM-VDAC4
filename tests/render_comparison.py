"""Visual report from the two real framebuffers, no reconstructed game art."""
from pathlib import Path
from PIL import Image,ImageChops,ImageDraw,ImageFont
import json
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'build/host'
a=Image.open(out/'renderer-software.ppm').convert('RGB')
b=Image.open(out/'renderer-bl616.ppm').convert('RGB')
diff=ImageChops.difference(a,b)
different=sum(x!=(0,0,0) for x in diff.getdata())
for name,img in [('software',a),('bl616',b),('difference',diff)]:img.save(out/f'renderer-{name}.png')
font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',20)
small=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',16)
canvas=Image.new('RGB',(1312,710),'#151920');d=ImageDraw.Draw(canvas)
d.text((16,12),'DOOM: один кадр, два растеризатора',font=font,fill='white')
for x,title,img in [(16,'Оригинальный программный рендер',a),(672,'Код BL616 в стенде на ПК',b)]:
    d.text((x,52),title,font=small,fill='#cdd5df')
    canvas.paste(img.resize((624,390),Image.Resampling.NEAREST),(x,80))
canvas.paste(diff,(16,495))
d.text((360,500),f'Различий: {different} из 64 000 пикселей',font=font,fill='white')
d.text((360,536),'Слева внизу — абсолютная разность RGB. Чёрный = совпадение.',font=small,fill='#cdd5df')
d.text((360,566),'Стенд исполняет исходники прошивки BL616 и поток команд ESP32.',font=small,fill='#cdd5df')
d.text((360,596),'Это проверка изображения; скорость на железе здесь не измеряется.',font=small,fill='#cdd5df')
canvas.save(out/'renderer-comparison.png')
(out/'renderer-comparison.json').write_text(json.dumps({'size':[320,200],'different_pixels':different,'capture':'same frame, same engine state, software versus actual BL616 source on host'},indent=2))
print(f'different pixels: {different}/64000')
assert different==0
