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
                if r['status'] != 'ошибка сети':
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
        pick, crop, grays, got = None, None, [], 0
        for n, u in enumerate(urls[:6]):
            d = fetch(u)
            if d is None:
                continue
            got += 1
            k = mp.photo_kind(d)
            if k == 'pack':
                pick = (n, d, False); break
            if k == 'crop' and crop is None:
                crop = (n, d, True)
            if k == 'gray' and len(grays) < 2:
                grays.append((n, d))
        pick = pick or crop
        pre = None
        if not pick:
            # студийное фото на светлом однотонном фоне: вырезаем, если вокруг нет надписей
            for n, d in grays:
                try:
                    full = mp.cut_out_full(d)
                except Exception as e:
                    print('   ошибка вырезания:', e); continue
                if mp.clean_cutout(d, full):
                    box = full.getchannel('A').point(lambda v: 255 if v > 20 else 0).getbbox()
                    pick, pre = (n, d, mp.touches_bottom(full)), (full.crop(box) if box else full)
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
                fun = mp.is_fun(art) and mp.load_backgrounds(kind + '_fun')
                bgs = mp.load_backgrounds(kind + '_fun') if fun else mp.load_backgrounds(kind)
                if bgs:
                    bgp = mp.pick_bg(product, bgs, f'{art}-1', kind, cropped, base=mp.FUN_SCALE if fun else 1.0)
                    bg, tag = mp.prepare_bg(bgp), os.path.splitext(os.path.basename(bgp))[0]
                else:
                    bg, tag = mp.fallback_backgrounds()[0], 'bg'
                img = mp.compose(product, bg, kind, f'{art}-1', cropped, bool(fun))
                pp = mp.place(product, kind, f'{art}-1', cropped, mp.FUN_SCALE if fun else 1.0)[0]
                if mp.product_share(pp) < (0.22 if kind == 'jacket' else 0.15):
                    raise RuntimeError(f'товар мелкий: {mp.product_share(pp):.0%} кадра')
                fn = f'{safe(art)}_{kind[0]}{"f" if fun else ""}{tag}_{mp.VERSION}.jpg'
                img.save(os.path.join(WORK, 'img', fn), quality=86, optimize=True)
                final = [PAGES + 'img/' + fn] + [u for j, u in enumerate(urls) if j != n][:MAX_PHOTOS - 1]
                status = 'новый фон' + (' (модель по пояс)' if cropped else '') + (', весёлый' if fun else '')
            except Exception as e:
                print('   ошибка обработки:', e)
        print('  ', status)
        out.append([art, status, ' | '.join(final)])
    with open(os.path.join(WORK, 'part.csv'), 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows([['article', 'status', 'photo_urls']] + out)

if __name__ == '__main__':
    main()
