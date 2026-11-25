# Убирает белый фон с главного фото товара и ставит товар на новый фон.
# Запуск: python3 make_photos.py
import csv, io, os, random, sys, glob
from PIL import Image, ImageFilter, ImageDraw, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
W, H = 1200, 1200       # квадрат 1:1, как лента Авито в телефоне
SIZE = H
VERSION = 'v6'
SAFE = 0.13             # поля, которые может срезать XL-карточка 4:3: товар и плашка внутри
BADGE = ('Новый, оригинал', 'Чек и гарантия магазина')
FUN_PCT = 0             # весёлые фоны выключены (владелец решил: несерьёзно)
CITY_PCT = 50           # доля курток с городским фоном в стиле Петербурга
FUN_BLUR = 4            # лёгкое размытие весёлого фона: зверь узнаётся, товар отделяется
FUN_SCALE = 0.85        # на весёлом фоне товар чуть меньше, чтобы персонажи по краям были видны
VARIANTS = 3           # сколько вариантов фона делать на каждый товар

def load_backgrounds(kind):
    files = []
    for folder in (os.path.join(HERE, 'backgrounds', kind), os.path.join(HERE, 'backgrounds')):
        for ext in ('jpg', 'jpeg', 'png', 'webp', 'JPG', 'JPEG', 'PNG'):
            files += glob.glob(os.path.join(folder, '*.' + ext))
    return sorted(set(files))

def is_fun(article):
    # постоянный жребий по артикулу: один и тот же товар всегда в одной группе
    import hashlib
    return int(hashlib.md5(article.encode()).hexdigest(), 16) % 100 < FUN_PCT

def is_city(article):
    import hashlib
    return int(hashlib.md5(('city-' + article).encode()).hexdigest(), 16) % 100 < CITY_PCT

def fallback_backgrounds():
    # если своих фонов нет, делаем три спокойных фона с лёгкой фактурой
    res = []
    for top, bottom in (((214, 208, 200), (178, 170, 160)), ((200, 203, 206), (150, 154, 158)), ((222, 205, 182), (186, 160, 128))):
        g = Image.new('RGB', (W, H))
        d = ImageDraw.Draw(g)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        noise = Image.effect_noise((W, H), 18).convert('RGB')
        res.append(Image.blend(g, noise, 0.06))
    return res

def prepare_bg(src):
    im = src if isinstance(src, Image.Image) else Image.open(src)
    im = ImageOps.exif_transpose(im).convert('RGB')
    return ImageOps.fit(im, (W, H), Image.LANCZOS)

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

def focus_product(product, kind, cropped=False):
    # модель в полный рост: куртка тогда занимает треть фигуры и выходит мелкой.
    # Оставляем голову и куртку, ноги срезаем нижним краем кадра, как в портрете по пояс
    if kind != 'jacket' or product.height / product.width <= 1.7:
        return product, cropped
    h = min(product.height, int(max(0.55 * product.height, 1.4 * product.width)))
    top = product.crop((0, 0, product.width, h))
    box = top.getchannel('A').point(lambda v: 255 if v > 40 else 0).getbbox()
    if box:
        top = top.crop((box[0], box[1], box[2], h))
    return top, True

def big_enough(p, kind):
    # товар крупный: обувь шириной от 50% кадра, куртка высотой от 55% и площадью рамки от 28%
    box = p.getchannel('A').point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return False, 'пусто'
    w, h = (box[2] - box[0]) / W, (box[3] - box[1]) / H
    if kind == 'shoes':
        return w >= 0.50, f'ширина {w:.0%}'
    return h >= 0.55 and w * h >= 0.28, f'высота {h:.0%}, рамка {w * h:.0%}'

def layout(product, kind, seed, cropped=False, shrink=1.0):
    # товар крупно, почти на весь кадр
    rnd = random.Random(seed)
    pw, ph = product.size
    if kind == 'shoes':
        scale = min(0.86 * W / pw, 0.70 * H / ph)
    elif cropped:
        # модель: по ширине почти во весь кадр, голова у верхнего края, низ уходит за кадр
        scale = min(1.04 * H / ph, 0.95 * W / pw)
    else:
        scale = min(0.92 * H / ph, 0.90 * W / pw)
    scale *= shrink * rnd.uniform(0.99, 1.01)
    p = product.resize((max(1, int(pw * scale)), max(1, int(ph * scale))), Image.LANCZOS)
    x = (W - p.width) // 2
    if kind == 'shoes':
        y = int(H * 0.86) - p.height
    elif cropped:
        y = int(0.03 * H) if p.height >= 0.97 * H else H - p.height
    else:
        y = (H - p.height) // 2
    return p, x, y, False

def _font(bold, size):
    from PIL import ImageFont
    here = os.path.dirname(os.path.abspath(__file__))
    names = ['DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf']
    for d in (os.path.join(here, 'fonts'), '/usr/share/fonts/truetype/dejavu', '/Library/Fonts', '/System/Library/Fonts/Supplemental'):
        for n in names + (['Arial Bold.ttf'] if bold else ['Arial.ttf']):
            if os.path.exists(os.path.join(d, n)):
                return ImageFont.truetype(os.path.join(d, n), size)
    return ImageFont.load_default()

def badge_size():
    from PIL import ImageDraw
    f1, f2 = _font(True, 58), _font(False, 38)
    d = ImageDraw.Draw(Image.new('L', (1, 1)))
    w = max(d.textlength(BADGE[0], font=f1), d.textlength(BADGE[1], font=f2)) + 2 * 30 + 16
    return int(w), 58 + 38 + 16 + 2 * 24

def badge_spots(p, x, y):
    # плашка в верхних углах (внизу карточки Авито ставит свой ярлык «Оплата частями»)
    bw, bh = badge_size()
    m = int(0.035 * W)
    spots = [(m, m), (W - m - bw, m)]
    a = Image.new('L', (W, H), 0); a.paste(p.getchannel('A'), (x, y))
    res = []
    for sx, sy in spots:
        box = a.crop((sx, sy, sx + bw, sy + bh))
        res.append((sum(box.point(lambda v: 1 if v > 40 else 0).getdata()) / (bw * bh), sx, sy))
    return res

def place(product, kind, seed, cropped=False, base=1.0):
    # плашка не должна закрывать товар и лицо: модель опускаем ниже плашки, остальное чуть уменьшаем
    _, bh = badge_size()
    for shrink in (1.0, 0.93, 0.86):
        p, x, y, st = layout(product, kind, seed, cropped, base * shrink)
        spots = badge_spots(p, x, y)
        lo = min(c for c, _, _ in spots)
        if lo <= 0.04:
            break
        y2 = int(0.035 * W) + bh + 14
        if kind == 'jacket' and y < y2 and (cropped or y2 + p.height <= 0.99 * H):
            y = y2
            spots = badge_spots(p, x, y)
            lo = min(c for c, _, _ in spots)
            if lo <= 0.04:
                break
    free = [(sx, sy) for c, sx, sy in spots if c <= lo + 0.03]
    return p, x, y, st, free

def busy(bg, sx, sy):
    # насколько пёстрый фон в углу (там может быть зверь или огни)
    from PIL import ImageStat
    bw, bh = badge_size()
    g = bg.convert('L').crop((sx, sy, sx + bw, sy + bh)).filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(g).mean[0]

def draw_badge(canvas, bx, by):
    from PIL import ImageDraw
    f1, f2 = _font(True, 58), _font(False, 38)
    bw, bh = badge_size()
    ov = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    sh = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([bx + 3, by + 6, bx + bw + 3, by + bh + 6], radius=24, fill=(0, 0, 0, 80))
    sh = sh.filter(ImageFilter.GaussianBlur(8))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=24, fill=(255, 255, 255, 238))
    d.rectangle([bx, by + 22, bx + 10, by + bh - 22], fill=(0, 150, 70, 255))
    d.text((bx + 30 + 16, by + 20), BADGE[0], font=f1, fill=(20, 24, 30, 255))
    d.text((bx + 30 + 16, by + 20 + 58 + 14), BADGE[1], font=f2, fill=(55, 60, 70, 255))
    out = Image.alpha_composite(canvas.convert('RGBA'), sh)
    return Image.alpha_composite(out, ov)

_BG_CACHE = {}

def bg_gray(path):
    if path not in _BG_CACHE:
        _BG_CACHE[path] = prepare_bg(path).convert('L')
    return _BG_CACHE[path]

def contrast_on(bg_l, p, x, y):
    # яркость фона в полосе вокруг силуэта товара, отдельно сверху и снизу
    from PIL import ImageStat
    a = p.getchannel('A').point(lambda v: 255 if v > 40 else 0)
    pad = 40
    big = Image.new('L', (p.width + 2 * pad, p.height + 2 * pad), 0)
    big.paste(a, (pad, pad))
    ring = Image.eval(big.filter(ImageFilter.MaxFilter(31)), lambda v: v)
    ring = Image.composite(Image.new('L', big.size, 0), ring, big)  # кольцо = расширенный силуэт минус сам товар
    region = bg_l.crop((x - pad, y - pad, x - pad + big.width, y - pad + big.height))
    inside = Image.new('L', bg_l.size, 255).crop((x - pad, y - pad, x - pad + big.width, y - pad + big.height))
    ring = Image.composite(ring, Image.new('L', big.size, 0), inside)  # за краем кадра не считаем
    pl = ImageStat.Stat(p.convert('L'), mask=a).mean[0]
    half = big.height // 2
    res = []
    for box in ((0, 0, big.width, half), (0, half, big.width, big.height)):
        m = ring.crop(box)
        if m.getbbox():
            res.append(abs(ImageStat.Stat(region.crop(box), mask=m).mean[0] - pl))
    return min(res) if res else 0

def pick_bg(product, bgs, seed, kind='shoes', cropped=False, min_diff=50, base=1.0):
    # светлый товар на тёмный фон, тёмный на светлый: проверяем фон прямо вокруг силуэта
    p, x, y, _, _ = place(product, kind, seed, cropped, base)
    scored = sorted(((contrast_on(bg_gray(b), p, x, y), b) for b in bgs), reverse=True)
    good = sorted(b for d, b in scored if d >= min_diff)
    if not good:
        good = [b for d, b in scored[:max(1, len(scored) // 3)]]
    return random.Random(seed).choice(good)

def compose(product, bg, kind, seed, cropped=False, fun=False):
    if fun and FUN_BLUR:
        bg = bg.filter(ImageFilter.GaussianBlur(FUN_BLUR))
    canvas = bg.copy()
    p, x, y, standing, free = place(product, kind, seed, cropped, FUN_SCALE if fun else 1.0)
    bx, by = min(free, key=lambda s_: busy(bg, *s_))
    p = match_tone(p, bg)
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    if (kind == 'shoes' and not cropped) or standing:
        d = ImageDraw.Draw(shadow)
        w0 = 0.04 if kind == 'shoes' else 0.15
        d.ellipse([x + p.width * w0, y + p.height - 24, x + p.width * (1 - w0), y + p.height + 20], fill=(0, 0, 0, 125))
        shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    else:
        alpha = p.getchannel('A')
        sh = Image.new('RGBA', p.size, (0, 0, 0, 110))
        sh.putalpha(alpha.point(lambda a: int(a * 0.45)))
        shadow.paste(sh, (x + 12, y + 22), sh)
        shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas = Image.alpha_composite(canvas.convert('RGBA'), shadow)
    canvas.paste(p, (x, y), p)
    if BADGE:
        canvas = draw_badge(canvas, bx, by)
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
    # ровный светлый студийный фон (серый, бежевый): края однотонные
    def plain(e):
        e = e.reshape(-1, 3)
        return e.mean() >= 165 and e.std() <= 15
    if plain(a[:, :k]) and plain(a[:, -k:]):
        return 'gray'
    return None

def clean_cutout(data, product_full):
    # фон вокруг товара однотонный, без надписей и инфографики
    import numpy as np
    im = np.asarray(Image.open(io.BytesIO(data)).convert('L')).astype(int)
    al = np.asarray(product_full.getchannel('A'))
    if al.shape != im.shape:
        return False
    bgp = im[al < 10]
    if bgp.size < 1000:
        return False
    med = np.median(bgp)
    if (np.abs(bgp - med) > 28).mean() >= 0.015:
        return False
    # вырезалось одним куском: отдельные значки и надписи рядом с товаром не допускаем
    try:
        from scipy import ndimage
        lab, n = ndimage.label(al > 60)
        if n > 1:
            sizes = np.bincount(lab.ravel())[1:]
            if np.sort(sizes)[:-1].sum() > 0.003 * al.size:
                return False
    except ImportError:
        pass
    return True

def touches_bottom(product_full):
    import numpy as np
    al = np.asarray(product_full.getchannel('A'))
    return (al[-4:] > 40).mean() > 0.05

def cut_out_full(img_bytes):
    # вырезка без обрезки по краям, чтобы сверить с исходником
    global _session
    from rembg import remove, new_session
    if _session is None:
        _session = new_session(MODEL)
    out = remove(img_bytes, session=_session)
    return Image.open(io.BytesIO(out)).convert('RGBA')

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
