"""Reproducible application mark: split play symbol / motion on a timeline."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
def main():
    assets=ROOT/'assets'; assets.mkdir(exist_ok=True)
    size=1024
    im=Image.new('RGBA',(size,size)); px=im.load()
    mask=Image.new('L',im.size); ImageDraw.Draw(mask).rounded_rectangle((48,48,976,976),radius=220,fill=255)
    for y in range(size):
        for x in range(size):
            t=(x+y)/(size*2)
            px[x,y]=(int(94-43*t),int(102-43*t),int(245-56*t),mask.getpixel((x,y)))
    d=ImageDraw.Draw(im)
    # Two separated pieces suggest a play button cut into motion segments.
    d.polygon([(350,260),(565,384),(565,640),(350,764)],fill='#ffffff')
    d.polygon([(621,416),(790,512),(621,608)],fill='#a5f3fc')
    d.rounded_rectangle((246,330,282,694),radius=18,fill='#a5f3fc')
    d.rounded_rectangle((350,822,548,842),radius=10,fill='#b6baff')
    d.rounded_rectangle((580,822,674,842),radius=10,fill='#a5f3fc')
    im.save(assets/'icon.png')
    im.save(assets/'icon.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    im.save(assets/'icon.icns')
    im.resize((512,512),Image.Resampling.LANCZOS).save(assets/'icon-512.png')
    (assets/'icon.svg').write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
<defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#5e66f5"/><stop offset="1" stop-color="#333bbd"/></linearGradient></defs>
<rect x="48" y="48" width="928" height="928" rx="220" fill="url(#g)"/>
<path d="M350 260 565 384V640L350 764Z" fill="white"/>
<path d="M621 416 790 512 621 608Z" fill="#a5f3fc"/>
<rect x="246" y="330" width="36" height="364" rx="18" fill="#a5f3fc"/>
<rect x="350" y="822" width="198" height="20" rx="10" fill="#b6baff"/>
<rect x="580" y="822" width="94" height="20" rx="10" fill="#a5f3fc"/>
</svg>''')
if __name__=='__main__': main()
