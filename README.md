# ИИ Дайджест

Все новости про искусственный интеллект на одной странице — коротко, по разделам и с картинками.

**Разделы:** Модели · Продукты · Бизнес · Исследования · Право и этика · Железо · Разное · 📌 Закреплённые

- 15 источников: Хабр, 3DNews, iXBT, OpenAI, Google AI, DeepMind, Hugging Face, TechCrunch, The Verge, Ars Technica, MIT Technology Review, Wired, The Decoder, VentureBeat
- у каждой новости — картинка (из RSS или со страницы статьи), краткая суть в 1–2 предложения, источник и время
- фильтры: раздел, русские / мировые источники, период, поиск
- закладка на карточке закрепляет новость в разделе «Закреплённые» (хранится в браузере)
- светлая и тёмная тема, удобно с телефона

## Как это работает

`scripts/fetch_news.py` раз в 2 часа (GitHub Actions) скачивает RSS-ленты, вытаскивает картинки, сокращает текст, раскладывает по разделам и сохраняет в `data/news.json`. Страница `index.html` просто читает этот файл. Сервер не нужен.

## Запуск сайта (GitHub Pages)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Settings → Actions → General → Workflow permissions → Read and write permissions** (чтобы бот мог сохранять новости).
3. Сайт опубликуется сам при следующем обновлении (или: Actions → «Обновить новости» → Run workflow).

Через минуту сайт будет на `https://alihanerimbetov0-byte.github.io/ai-news/`.

## Перевод и пересказ на русском (по желанию)

Без настроек мировые новости показываются на английском с кнопкой «Перевести». Если добавить секрет
**Settings → Secrets and variables → Actions → New repository secret** с именем `ANTHROPIC_API_KEY`,
каждая новая новость будет пересказана Claude на русском в 1–2 предложения и точнее разложена по разделам.
Уже обработанные новости повторно не отправляются.

## Локально

```bash
python scripts/fetch_news.py
python -m http.server 8000
```
и откройте http://localhost:8000

Источники добавляются в список `FEEDS` в начале `scripts/fetch_news.py`.
