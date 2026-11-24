# Собирает результаты всех частей: фото в img/, ссылки в photo_urls.csv
import csv, glob, os, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res = {}
p = os.path.join(ROOT, 'photo_urls.csv')
if os.path.exists(p):
    with open(p, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            res[r['article']] = [r['article'], r['status'], r['photo_urls']]
os.makedirs(os.path.join(ROOT, 'img'), exist_ok=True)
new = 0
for part in glob.glob(os.path.join(ROOT, 'parts', '*', 'part.csv')):
    d = os.path.dirname(part)
    for fn in glob.glob(os.path.join(d, 'img', '*.jpg')):
        shutil.copy(fn, os.path.join(ROOT, 'img', os.path.basename(fn)))
    with open(part, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            res[r['article']] = [r['article'], r['status'], r['photo_urls']]
            new += 1
with open(p, 'w', newline='', encoding='utf-8') as f:
    csv.writer(f).writerows([['article', 'status', 'photo_urls']] + list(res.values()))
from collections import Counter
print('обработано сейчас:', new, '| всего в базе:', len(res), dict(Counter(v[1] for v in res.values())))

# сводная картинка новых фото этого запуска для быстрой проверки
try:
    from PIL import Image, ImageDraw
    fs = sorted(glob.glob(os.path.join(ROOT, 'parts', '*', 'img', '*.jpg')))[:60]
    if fs:
        S, cols = 240, 6
        rows = (len(fs) + cols - 1) // cols
        sh = Image.new('RGB', (S * cols, S * rows), 'white'); d = ImageDraw.Draw(sh)
        for i, f in enumerate(fs):
            x, y = (i % cols) * S, (i // cols) * S
            sh.paste(Image.open(f).convert('RGB').resize((S, S)), (x, y))
            d.rectangle([x, y, x + S, y + 14], fill=(0, 0, 0)); d.text((x + 3, y + 2), os.path.basename(f)[:34], fill=(255, 255, 255))
        sh.save(os.path.join(ROOT, 'preview.jpg'), quality=80)
except Exception as e:
    print('preview:', e)
