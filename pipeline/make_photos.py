# Убирает белый фон с главного фото товара и ставит товар на новый фон.
# Запуск: python3 make_photos.py
import csv, io, os, random, sys, glob
from PIL import Image, ImageFilter, ImageDraw, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
SIZE = 1200            # итоговое фото 1200x1200
VARIANTS = 3           # сколько вариантов фона делать на каждый товар

def load_backgrounds(kind):
    files = []
    for folder in (os.path.join(HERE, 'backgrounds', kind), os.path.join(HERE, 'backgrounds')):
        for ext in ('jpg', 'jpeg', 'png', 'webp', 'JPG', 'JPEG', 'PNG'):
            files += glob.glob(os.path.join(folder, '*.' + ext))
    return sorted(set(files))

def fallback_backgrounds():
    # если своих фонов нет, делаем три спокойных фона с лёгкой фактурой
    res = []
    for top, bottom in (((214, 208, 200), (178, 170, 160)), ((200, 203, 206), (150, 154, 158)), ((222, 205, 182), (186, 160, 128))):
        g = Image.new('RGB', (SIZE, SIZE))
        d = ImageDraw.Draw(g)
        for y in range(SIZE):
            t = y / SIZE
            d.line([(0, y), (SIZE, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        noise = Image.effect_noise((SIZE, SIZE), 18).convert('RGB')
        res.append(Image.blend(g, noise, 0.06))
    return res

def prepare_bg(src):
    im = src if isinstance(src, Image.Image) else Image.open(src)
    im = ImageOps.exif_transpose(im).convert('RGB')
    return ImageOps.fit(im, (SIZE, SIZE), Image.LANCZOS)

MODEL = 'birefnet-general-lite'   # лицензия MIT, можно для коммерции
_session = None

def cut_out(img_bytes):
    global _session
    from rembg import remove, new_session
    if _session is None:
        _session = new_session(MODEL)
    out = remove(img_bytes, session=_session)
    im = Image.open(io.BytesIO(out)).convert('RGBA')
    box = im.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
    return im.crop(box) if box else im

def match_tone(p, bg, strength=0.10):
    # чуть подтягиваем яркость и оттенок товара к фону, чтобы не выглядел вклеенным
    from PIL import ImageStat, ImageEnhance
    bm = ImageStat.Stat(bg.convert('RGB').resize((64, 64))).mean
    rgb, a = p.convert('RGB'), p.getchannel('A')
    pm = ImageStat.Stat(rgb, mask=a).mean
    bl, pl = sum(bm) / 3, max(sum(pm) / 3, 1)
    rgb = ImageEnhance.Brightness(rgb).enhance(1 + strength * (bl / pl - 1) * 0.5)
    tint = Image.new('RGB', rgb.size, tuple(int(c) for c in bm))
    rgb = Image.blend(rgb, tint, strength * 0.5)
    out = rgb.convert('RGBA'); out.putalpha(a)
    return out

def compose(product, bg, kind, seed, cropped=False):
    rnd = random.Random(seed)
    canvas = bg.copy()
    # размер товара на фото: куртки крупнее, обувь чуть меньше
    standing = kind == 'jacket' and not cropped and product.height / product.width > 1.8
    if standing:
        # модель в полный рост: ставим ногами на пол
        scale = 0.84 * SIZE / product.height * rnd.uniform(0.97, 1.02)
    elif cropped:
        # человек обрезан снизу: ставим по нижнему краю кадра, как обычный портрет
        scale = min(0.90 * SIZE / product.height, 0.96 * SIZE / product.width) * rnd.uniform(0.97, 1.0)
    else:
        target = (0.80 if kind == 'jacket' else 0.68) * SIZE * rnd.uniform(0.95, 1.03)
        scale = target / max(product.size)
    p = product.resize((int(product.width * scale), int(product.height * scale)), Image.LANCZOS)
    p = match_tone(p, bg)
    x = (SIZE - p.width) // 2 + (0 if cropped else rnd.randint(-25, 25))
    if standing:
        y = int(SIZE * 0.95) - p.height
    elif cropped:
        y = SIZE - p.height
    elif kind == 'shoes':
        # подошва стоит на полу: низ обуви на 88% высоты кадра
        y = int(SIZE * 0.88) - p.height + rnd.randint(-10, 10)
    else:
        y = (SIZE - p.height) // 2 + 15 + rnd.randint(-15, 15)
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    if (kind == 'shoes' and not cropped) or standing:
        # контактная тень под подошвой
        d = ImageDraw.Draw(shadow)
        w0 = 0.04 if kind == 'shoes' else 0.15
        d.ellipse([x + p.width * w0, y + p.height - 22, x + p.width * (1 - w0), y + p.height + 18], fill=(0, 0, 0, 120))
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    else:
        # мягкая тень на стене за курткой
        alpha = p.getchannel('A')
        sh = Image.new('RGBA', p.size, (0, 0, 0, 110))
        sh.putalpha(alpha.point(lambda a: int(a * 0.45)))
        shadow.paste(sh, (x + 12, y + 22), sh)
        shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas = Image.alpha_composite(canvas.convert('RGBA'), shadow)
    canvas.paste(p, (x, y), p)
    return canvas.convert('RGB')

def photo_kind(data):
    # 'pack'  товар на белом фоне со всех сторон
    # 'crop'  белый фон, но человек обрезан нижним краем кадра (модель по пояс)
    # None    серый фон, инфографика и прочее: не трогаем
    import numpy as np
    im = Image.open(io.BytesIO(data)).convert('RGB')
    im.thumbnail((400, 400))
    a = np.asarray(im).astype(int)
    k = max(4, a.shape[0] // 40)
    def white(e):
        # доля почти белых пикселей на краю кадра
        e = e.reshape(-1, 3)
        return (e.min(1) > 235).mean()
    top, left, right, bottom = white(a[:k]), white(a[:, :k]), white(a[:, -k:]), white(a[-k:])
    if min(top, left, right, bottom) >= 0.97:
        return 'pack'
    if top >= 0.97 and min(left, right) >= 0.85 and bottom < 0.97:
        return 'crop'
    return None

def main():
    import requests, shutil
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(HERE, 'items.csv'), encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    report = []
    for i, r in enumerate(rows, 1):
        art, kind = r['article'], r['type']
        urls = (r.get('photo_urls') or r.get('photo_url') or '').split()
        print(f'[{i}/{len(rows)}] {art} ...', flush=True)
        chosen, crop, first = None, None, None
        for n, url in enumerate(urls[:6], 1):
            try:
                data = requests.get(url, timeout=60, headers={'User-Agent': 'Mozilla/5.0'}).content
            except Exception as e:
                print('   ошибка скачивания:', e); continue
            if first is None:
                first = data
            k = photo_kind(data)
            if k == 'pack':
                chosen = (n, data, False); break
            if k == 'crop' and crop is None:
                crop = (n, data, True)
        chosen = chosen or crop
        if not chosen:
            # белого фото нет (модель, инфографика): оставляем первое фото как есть
            if first:
                open(os.path.join(OUT, f'{art}_original.jpg'), 'wb').write(first)
            print('   белого фото нет, оставила оригинал')
            report.append([art, '', 'оригинал'])
            continue
        n, data, cropped = chosen
        try:
            product = cut_out(data)
        except Exception as e:
            print('   ошибка вырезания:', e); continue
        bgs = load_backgrounds(kind)
        rnd = random.Random(art)
        if bgs:
            picked = rnd.sample(bgs, min(VARIANTS, len(bgs)))
            bg_imgs = [prepare_bg(b) for b in picked]
        else:
            bg_imgs = fallback_backgrounds()[:VARIANTS]
        for j, bg in enumerate(bg_imgs, 1):
            compose(product, bg, kind, f'{art}-{j}', cropped).save(os.path.join(OUT, f'{art}_{j}.jpg'), quality=90)
        product.save(os.path.join(OUT, f'{art}_cutout.png'))
        print(f'   взяла фото №{n}' + (' (модель по пояс)' if cropped else ''))
        report.append([art, n, 'модель по пояс' if cropped else 'новый фон'])
    with open(os.path.join(OUT, 'report.csv'), 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows([['article', 'photo_no', 'result']] + report)
    print('Готово. Фото в папке out, сводка в out/report.csv')

if __name__ == '__main__':
    main()
