# ادغام `articles-index.json` با جست‌وجوی یکپارچه

پس از کپی فایل‌ها:

```powershell
cd "C:\Users\hamid\Desktop\nazm-dad"
python .\tools\build_articles_index.py
```

سپس باید فایل زیر ساخته شود:

```text
assets\data\articles-index.json
```

## تست

```powershell
python -m py_compile .\tools\build_articles_index.py
Write-Host "compile exit:" $LASTEXITCODE

python .\tools\build_articles_index.py
Write-Host "build exit:" $LASTEXITCODE
```

در خروجی باید تعداد مواد هر نسخه نمایش داده شود.

## اضافه‌کردن به `search/index.html`

قبل از `search.js`:

```html
<script src="../assets/js/articles-search.js" defer></script>
<script src="../assets/js/favorites-store.js" defer></script>
<script src="../assets/js/search.js" defer></script>
```

## تغییر پیشنهادی در `search.js`

در بخش بارگذاری داده‌ها، فایل سوم را هم بخوانید:

```javascript
const [audioResponse, docsResponse, articlesResponse] = await Promise.all([
  fetch('../assets/data/audio.json', { cache: 'no-store' }),
  fetch('../assets/data/docs-metadata.json', { cache: 'no-store' }),
  fetch('../assets/data/articles-index.json', { cache: 'no-store' })
]);

const [audioData, docsData, articlesData] = await Promise.all([
  audioResponse.json(),
  docsResponse.json(),
  articlesResponse.json()
]);

const articleItems = (Array.isArray(articlesData.articles) ? articlesData.articles : []).map(article => ({
  id: `article-${article.id}`,
  type: 'article',
  title: article.label,
  description: article.excerpt || article.text || '',
  category: article.chapter || 'مواد قانون اساسی',
  status: `نسخه ${article.version}`,
  file: article.url,
  date: '',
  tags: [
    `ماده ${article.articleId}`,
    article.articleId,
    article.version,
    article.chapter
  ].filter(Boolean),
  articleIds: [article.articleId]
}));

state.allItems = [...documentItems, ...audioItems, ...articleItems];
```

برای نمایش badge نوع ماده نیز در `createCard`:

```javascript
if (item.type === 'article') {
  type.textContent = '§ ماده';
} else {
  type.textContent = item.type === 'audio' ? '🎵 صوتی' : '📄 سند';
}
```

و در CSS:

```css
.result-type-article {
  background: rgba(201, 168, 108, .14);
  color: #d9bf8a;
}
```

## نکته مهم درباره لینک مستقیم

اسکریپت لینک‌ها را به شکل زیر می‌سازد:

```text
../docs/0.5.md#article-12
```

این لینک زمانی بهترین نتیجه را می‌دهد که فایل Markdown همان anchor را داشته باشد.
اگر anchorهای فایل متفاوت باشند، خود جست‌وجو و نمایش نتیجه درست کار می‌کند ولی پرش مستقیم ممکن است به ابتدای فایل برود.
