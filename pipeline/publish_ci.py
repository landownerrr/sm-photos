# Обработка части товаров на сервере GitHub Actions.
# Делает главное фото на новом фоне, кладёт его в work/img, ссылки в work/part.csv.
import argparse, csv, os, random, re, sys, time
import make_photos as mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, 'work')
MAX_PHOTOS = 10
repo = os.environ.get('GITHUB_REPOSITORY', 'landownerrr/sm-photos')
owner, name = repo.split('/')
PAGES = f'https://{owner.lower()}.github.io/{name}/'

def safe(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')

def fetch(url, tries=3):
    import requests
    for t in range(tries):
        try:
            r = requests.get(url, timeout=40, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200 and r.content:
                return r.content
            print('   код ответа', r.status_code, url)
        except Exception as e:
            print('   ошибка сети:', str(e)[:120])
        time.sleep(3 * (t + 1))
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int, default=0)
    ap.add_argument('--shards', type=int, default=1)
    a = ap.parse_args()
    done = set()
    prev = os.path.join(ROOT, 'photo_urls.csv')
    if os.path.exists(prev) and os.environ.get('REDO') != 'true':
        with open(prev, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r['status'].startswith('новый фон'):
                    done.add(r['article'])
    with open(os.path.join(ROOT, 'items.csv'), encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    mine = [r for i, r in enumerate(rows) if i % a.shards == a.shard and r['article'] not in done]
    print(f'Часть {a.shard}: товаров {len(mine)}')
    os.makedirs(os.path.join(WORK, 'img'), exist_ok=True)
    out, fails_in_row = [], 0
    for i, r in enumerate(mine, 1):
        art, kind = r['article'], r['type']
        urls = r['photo_urls'].split()
        print(f'[{i}/{len(mine)}] {art}', flush=True)
        # берём ПЕРВОЕ по порядку фото, которое можно вырезать: у Спортмастера первым идёт вид спереди,
        # дальше бывают вид со спины и детали, их главным ставить нельзя
        pick, pre, got = None, None, 0
        if r.get('style') == 'p2' and (r.get('gallery') or '').strip() == '1':
            # профиль 2: сводка всех фото галереи с номерами, чтобы выбрать главное вручную
            from PIL import Image, ImageDraw
            ims = []
            for n, u in enumerate(urls[:10]):
                d = fetch(u)
                if d is not None:
                    im = Image.open(__import__('io').BytesIO(d)).convert('RGB'); im.thumbnail((400, 400)); ims.append((n, im))
            sh = Image.new('RGB', (5 * 400, 2 * 430), 'white'); dr = ImageDraw.Draw(sh)
            for j, (n, im) in enumerate(ims):
                x, y = (j % 5) * 400, (j // 5) * 430
                sh.paste(im, (x + (400 - im.width) // 2, y + 30)); dr.text((x + 8, y + 6), f'#{n}', fill=(200, 0, 0))
            fn = f'{safe(art)}_gallery.jpg'
            sh.save(os.path.join(WORK, 'img', fn), quality=85)
            print('   галерея', len(ims))
            out.append([art, 'новый фон галерея', PAGES + 'img/' + fn])
            continue
        if r.get('style') == 'p2' and (r.get('pick') or '').strip().isdigit():
            # профиль 2: главное фото задано вручную по номеру в галерее
            n = int(r['pick'])
            d = fetch(urls[n]) if n < len(urls) else None
            if d is not None:
                got += 1
                full = mp.cut_out_full(d)
                box = full.getchannel('A').point(lambda v: 255 if v > 20 else 0).getbbox()
                pick, pre = (n, d, mp.touches_bottom(full)), (full.crop(box) if box else full)
        if pick is None and r.get('style') == 'p2' and (r.get('orig') or '').strip().isdigit():
            # профиль 2: берём указанное фото Спортмастера как есть, только квадрат и синяя полоса
            n = int(r['orig'])
            d = fetch(urls[n]) if n < len(urls) else None
            if kind == 'shoes':
                # обувь берём только целиком: если на фото она обрезана краем кадра, смотрим следующие фото
                for m in range(n, min(len(urls), n + 6)):
                    dm = d if m == n else fetch(urls[m])
                    if dm is not None and not mp.shoe_cut_off(dm):
                        n, d = m, dm
                        break
            if d is not None:
                fn = f'{safe(art)}_orig{n}_{mp.VERSION}.jpg'
                mp.compose_orig_p2(d, r.get('badge'), kind).save(os.path.join(WORK, 'img', fn), quality=88, optimize=True)
                final = [PAGES + 'img/' + fn] + [u for j, u in enumerate(urls) if j != n][:MAX_PHOTOS - 1]
                print('   новый фон P2, оригинал с полосой')
                out.append([art, 'новый фон P2, оригинал с полосой', ' | '.join(final)])
                continue
        mp.SHOE_PAIR = (r.get('pref') == 'pair')
        if pick is None and r.get('style') == 'p2' and r.get('pref') in ('product', 'pair'):
            # профиль 2: ищем в галерее фото просто товара, без человека
            # обувь: сначала второе фото (обычно ракурс под углом), потом остальные по порядку
            order = list(range(min(10, len(urls))))
            if kind == 'shoes' and len(order) > 1:
                order = [1, 0] + order[2:]
            for n in order:
                u = urls[n]
                d = fetch(u)
                if d is None:
                    continue
                got += 1
                k = mp.photo_kind(d)
                if k not in ('pack', 'gray'):
                    continue
                try:
                    full = mp.cut_out_full(d)
                except Exception as e:
                    print('   ошибка вырезания:', e); continue
                if k == 'gray' and not mp.clean_cutout(d, full):
                    continue
                box = full.getchannel('A').point(lambda v: 255 if v > 20 else 0).getbbox()
                cut = full.crop(box) if box else full
                # человек в полный рост или со спины высокий и узкий: такие фото пропускаем
                if mp.has_person(cut) or cut.height > 1.5 * cut.width or (kind == 'jacket' and mp.too_close(cut)):
                    continue
                if kind == 'shoes' and n == 1 and not (1.05 <= cut.width / max(1, cut.height) <= 1.85):
                    continue  # второе фото не ракурс под углом (вид сверху, подошва и т.п.)
                pick, pre = (n, d, False), cut
                break
        skip = int(r['skip']) if (r.get('skip') or '').strip().isdigit() else 0
        for n, u in enumerate(urls[:6 + skip] if pick is None else []):
            if n < skip:
                continue  # первые фото пропускаем по указанию (например, крупный план лица)
            d = fetch(u)
            if d is None:
                continue
            got += 1
            k = mp.photo_kind(d)
            if r.get('style') == 'p2' and kind == 'jacket' and k in ('pack', 'crop'):
                # профиль 2: крупный план лица пропускаем, нужна куртка
                try:
                    cut = mp.cut_out(d)
                except Exception as e:
                    print('   ошибка вырезания:', e); continue
                if mp.too_close(cut):
                    print('   крупный план лица, беру следующее фото'); continue
                pick, pre = (n, d, k == 'crop'), cut
                break
            if k == 'pack':
                pick = (n, d, False); break
            if k == 'crop':
                pick = (n, d, True); break
            if k == 'gray':
                # студийное фото на светлом однотонном фоне: вырезаем, если вокруг нет надписей
                try:
                    full = mp.cut_out_full(d)
                except Exception as e:
                    print('   ошибка вырезания:', e); continue
                if mp.clean_cutout(d, full):
                    box = full.getchannel('A').point(lambda v: 255 if v > 20 else 0).getbbox()
                    cut = full.crop(box) if box else full
                    if r.get('style') == 'p2' and kind == 'jacket' and mp.too_close(cut):
                        print('   крупный план лица, беру следующее фото'); continue
                    pick, pre = (n, d, mp.touches_bottom(full)), cut
                    break
        final, status = urls[:MAX_PHOTOS], 'оригинал'
        if got == 0:
            status = 'ошибка сети'
            fails_in_row += 1
            if fails_in_row >= 3 and i == fails_in_row:
                print('Спортмастер не отдаёт фото этому серверу, останавливаю часть')
                out.append([art, status, ' | '.join(final)])
                break
        else:
            fails_in_row = 0
        if pick:
            n, d, cropped = pick
            try:
                product = pre if pre is not None else mp.cut_out(d)
                product, cropped = mp.focus_product(product, kind, cropped)
                if r.get('style') == 'p2':
                    # профиль 2: студийные фоны и своя плашка, имя файла с меткой p2
                    bgs = mp.load_backgrounds(kind + '_p2')
                    bgp = mp.pick_bg_p2(product, bgs, f'{art}-p2', kind, cropped)
                    img = mp.compose_p2(product, mp.prepare_bg(bgp), kind, f'{art}-p2', cropped, r.get('badge'))
                    pp = mp.place_p2(product, kind, f'{art}-p2', cropped)[0]
                    ok, why = mp.big_enough(pp, kind)
                    if not ok:
                        raise RuntimeError(f'товар мелкий ({why}), оставляю оригинал')
                    tag = os.path.splitext(os.path.basename(bgp))[0]
                    fn = f'{safe(art)}_{tag}_{mp.VERSION}.jpg'
                    img.save(os.path.join(WORK, 'img', fn), quality=88, optimize=True)
                    final = [PAGES + 'img/' + fn] + [u for j, u in enumerate(urls) if j != n][:MAX_PHOTOS - 1]
                    status = 'новый фон P2, ' + ('с человеком' if mp.has_person(product) else 'товар') + (' (по пояс)' if cropped else '')
                    print('  ', status)
                    out.append([art, status, ' | '.join(final)])
                    continue
                fun = mp.is_fun(art) and mp.load_backgrounds(kind + '_fun')
                city_bgs = mp.load_backgrounds(kind + '_city') if (not fun and mp.is_city(art)) else []
                bgs = mp.load_backgrounds(kind + '_fun') if fun else (city_bgs or mp.load_backgrounds(kind))
                if bgs:
                    bgp = mp.pick_bg(product, bgs, f'{art}-1', kind, cropped, base=mp.FUN_SCALE if fun else 1.0)
                    bg, tag = mp.prepare_bg(bgp), os.path.splitext(os.path.basename(bgp))[0]
                else:
                    bg, tag = mp.fallback_backgrounds()[0], 'bg'
                img = mp.compose(product, bg, kind, f'{art}-1', cropped, bool(fun))
                pp = mp.place(product, kind, f'{art}-1', cropped, mp.FUN_SCALE if fun else 1.0)[0]
                ok, why = mp.big_enough(pp, kind)
                if not ok:
                    raise RuntimeError(f'товар мелкий ({why}), оставляю оригинал')
                fn = f'{safe(art)}_{kind[0]}{"f" if fun else ("c" if city_bgs else "")}{tag}_{mp.VERSION}.jpg'
                img.save(os.path.join(WORK, 'img', fn), quality=86, optimize=True)
                final = [PAGES + 'img/' + fn] + [u for j, u in enumerate(urls) if j != n][:MAX_PHOTOS - 1]
                status = 'новый фон' + (' (модель по пояс)' if cropped else '') + (', весёлый' if fun else (', город' if city_bgs else ''))
            except Exception as e:
                print('   ошибка обработки:', e)
        print('  ', status)
        out.append([art, status, ' | '.join(final)])
    with open(os.path.join(WORK, 'part.csv'), 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows([['article', 'status', 'photo_urls']] + out)

if __name__ == '__main__':
    main()
