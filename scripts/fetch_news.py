"""Собирает новости про ИИ из RSS-лент и пишет их в data/news.json.

Работает только на стандартной библиотеке Python. Если задан ANTHROPIC_API_KEY
и установлен пакет `anthropic`, новые англоязычные и длинные новости
дополнительно переводятся и сжимаются Claude до 1–2 предложений на русском.

Запуск:  python scripts/fetch_news.py
"""

from __future__ import annotations

import concurrent.futures as cf
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news.json"

UA = "Mozilla/5.0 (compatible; AI-News-Digest/1.0)"
MAX_AGE_DAYS = 10        # старше — выкидываем
MAX_PER_FEED = 15        # не больше N свежих новостей с одной ленты за запуск
MAX_ITEMS = 400          # всего в файле
SUMMARY_LEN = 220

# lang: язык ленты; ai_only: лента общая, берём только новости с ИИ-словами
FEEDS = [
    # --- русскоязычные
    {"name": "Хабр · ИИ", "url": "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/", "lang": "ru"},
    {"name": "Хабр · ML", "url": "https://habr.com/ru/rss/hubs/machine_learning/articles/", "lang": "ru"},
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "lang": "ru", "ai_only": True},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "lang": "ru", "ai_only": True},
    # --- лаборатории и компании
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "lang": "en"},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "lang": "en"},
    {"name": "DeepMind", "url": "https://deepmind.google/blog/rss.xml", "lang": "en"},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "lang": "en"},
    # --- медиа
    {"name": "TechCrunch", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en"},
    {"name": "Ars Technica", "url": "https://arstechnica.com/ai/feed/", "lang": "en"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en"},
    {"name": "Wired", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en"},
    {"name": "The Decoder", "url": "https://the-decoder.com/feed/", "lang": "en"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/category/ai/feed/", "lang": "en"},
]

# Разделы сайта. Порядок важен: первый совпавший раздел побеждает.
CATEGORIES = [
    ("models", "Модели", r"\b(gpt|claude|gemini|llama|mistral|qwen|deepseek|grok|sora|veo|o\d|llm|модел\w*|model|release[sd]?|релиз|weights|open[- ]source|бенчмарк|benchmark)"),
    ("hardware", "Железо", r"\b(nvidia|gpu|chip|чип\w*|tpu|amd|intel|tsmc|blackwell|h100|h200|b200|datacenter|data center|дата-?центр\w*|процессор\w*|ускорител\w*|суперкомпьютер\w*)"),
    ("policy", "Право и этика", r"\b(regulat\w*|law|lawsuit|court|ban|act|policy|copyright|safety|ethic\w*|закон\w*|суд\w*|регулир\w*|запрет\w*|авторск\w*|этик\w*|безопасност\w*|government|правительств\w*)"),
    ("business", "Бизнес", r"\b(funding|raises?|raised|valuation|acqui\w*|ipo|investment|invest\w*|billion|million|startup|deal|revenue|инвест\w*|млрд|млн|стартап\w*|сделк\w*|покупк\w*|выручк\w*|оценк\w*|ceo)"),
    ("research", "Исследования", r"\b(research\w*|paper|study|arxiv|scientists?|исследова\w*|учён\w*|ученые|статья|научн\w*|dataset|датасет\w*|algorithm|алгоритм\w*)"),
    ("tools", "Продукты", r"\b(app|feature|tool|launch\w*|update|assistant|agent\w*|plugin|api|приложени\w*|функци\w*|сервис\w*|инструмент\w*|агент\w*|ассистент\w*|обновлени\w*|запуст\w*)"),
]
CATEGORY_IDS = [c[0] for c in CATEGORIES] + ["other"]

AI_WORDS = re.compile(
    r"(\bии\b|\bai\b|искусствен\w* интеллект|нейросет\w*|нейронн\w*|машинн\w* обучени|"
    r"chatgpt|openai|gpt|claude|anthropic|gemini|llm|deepseek|qwen|llama|mistral|"
    r"gigachat|гигачат|yandexgpt|алиса|midjourney|stable diffusion|sora|copilot|grok)",
    re.I,
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


# ---------------------------------------------------------------- helpers

def fetch(url: str, timeout: int = 30, tries: int = 2) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt == tries - 1:
                raise


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    # хвосты вида "The post X appeared first on Y." и "Читать далее"
    s = re.sub(r"(The post .* appeared first on .*|Читать далее.*|Read more.*|Continue reading.*)$", "", s).strip()
    return s


def shorten(text: str, limit: int = SUMMARY_LEN) -> str:
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    if not out:
        out = text[:limit].rsplit(" ", 1)[0] + "…"
    return out


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def first_img(raw_html: str) -> str | None:
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", raw_html or "", re.I)
    return html.unescape(m.group(1)) if m else None


def og_image(url: str) -> str | None:
    try:
        page = fetch(url, timeout=12, tries=1)[:300_000].decode("utf-8", "ignore")
    except Exception:
        return None
    for pat in (
        r"<meta[^>]+property=[\"']og:image(?::secure_url)?[\"'][^>]+content=[\"']([^\"']+)",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image",
        r"<meta[^>]+name=[\"']twitter:image[\"'][^>]+content=[\"']([^\"']+)",
    ):
        m = re.search(pat, page, re.I)
        if m:
            return html.unescape(m.group(1))
    return None


def categorize(text: str) -> str:
    for cid, _label, pat in CATEGORIES:
        if re.search(pat, text, re.I):
            return cid
    return "other"


def item_id(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:12]


def norm_title(t: str) -> str:
    return re.sub(r"\W+", "", t.lower())[:60]


# ---------------------------------------------------------------- parsing

def parse_feed(feed: dict) -> list[dict]:
    root = ET.fromstring(fetch(feed["url"]))
    entries = root.findall(".//item") or root.findall(".//atom:entry", NS)
    items = []
    for e in entries:
        title = strip_html(e.findtext("title") or e.findtext("atom:title", namespaces=NS) or "")
        link = (e.findtext("link") or "").strip()
        if not link:
            le = e.find("atom:link[@rel='alternate']", NS)
            if le is None:
                le = e.find("atom:link", NS)
            link = le.get("href", "") if le is not None else ""
        raw = (
            e.findtext("content:encoded", namespaces=NS)
            or e.findtext("description")
            or e.findtext("atom:summary", namespaces=NS)
            or e.findtext("atom:content", namespaces=NS)
            or ""
        )
        date = parse_date(
            e.findtext("pubDate")
            or e.findtext("atom:published", namespaces=NS)
            or e.findtext("atom:updated", namespaces=NS)
            or e.findtext("{http://purl.org/dc/elements/1.1/}date")
        )
        image = None
        for tag in ("media:content", "media:thumbnail"):
            for m in e.findall(tag, NS):
                if m.get("url") and (m.get("medium") in (None, "image") or tag == "media:thumbnail"):
                    image = m.get("url")
                    break
            if image:
                break
        if not image:
            enc = e.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                image = enc.get("url")
        if not image:
            image = first_img(raw)

        if not title or not link:
            continue
        summary = shorten(strip_html(raw))
        if feed.get("ai_only") and not AI_WORDS.search(f"{title} {summary}"):
            continue
        items.append({
            "id": item_id(link),
            "title": title,
            "summary": summary,
            "link": link,
            "image": image,
            "source": feed["name"],
            "lang": feed["lang"],
            "date": date.isoformat() if date else None,
            "category": categorize(f"{title} {summary}"),
        })
    return items


# ---------------------------------------------------------------- Claude (опционально)

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORY_IDS},
                },
                "required": ["id", "title", "summary", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

AI_PROMPT = """Ты редактор русскоязычного дайджеста новостей об ИИ. Для каждой новости верни:
- title: короткий понятный заголовок на русском (до 90 символов), без кликбейта;
- summary: суть простыми словами в 1–2 предложениях на русском (до 200 символов): что произошло и почему это важно;
- category: один из разделов — models (новые модели и релизы), tools (продукты и функции), business (деньги, сделки, компании),
  research (наука и исследования), policy (законы, суды, безопасность, этика), hardware (чипы, дата-центры), other.
Названия компаний и продуктов оставляй как есть (OpenAI, Claude, Gemini). Верни все id из входных данных.

Новости (JSON):
"""


def enrich_with_claude(items: list[dict]) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        import anthropic
    except ImportError:
        print("пакет anthropic не установлен — пропускаю ИИ-сжатие", file=sys.stderr)
        return
    todo = [it for it in items if not it.get("ai")]
    if not todo:
        return
    client = anthropic.Anthropic()
    by_id = {it["id"]: it for it in todo}
    for i in range(0, len(todo), 15):
        chunk = [{"id": it["id"], "title": it["title"], "text": it["summary"], "source": it["source"]}
                 for it in todo[i:i + 15]]
        try:
            resp = client.beta.messages.create(
                model="claude-opus-5-5",
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
                messages=[{"role": "user", "content": AI_PROMPT + json.dumps(chunk, ensure_ascii=False)}],
            )
            if resp.stop_reason == "refusal":
                continue
            text = next(b.text for b in resp.content if b.type == "text")
            for r in json.loads(text)["items"]:
                it = by_id.get(r["id"])
                if it:
                    it["title_orig"] = it["title"]
                    it["title"] = r["title"].strip()
                    it["summary"] = r["summary"].strip()
                    it["category"] = r["category"]
                    it["ai"] = True
        except anthropic.RateLimitError as e:
            print(f"Claude: лимит запросов, останавливаюсь: {e}", file=sys.stderr)
            break
        except anthropic.APIStatusError as e:
            print(f"Claude: ошибка API {e.status_code}: {e.message}", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"Claude: нет соединения: {e}", file=sys.stderr)
        except (StopIteration, json.JSONDecodeError, KeyError) as e:
            print(f"Claude: не разобрал ответ: {e}", file=sys.stderr)


# ---------------------------------------------------------------- main

def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    old = {}
    if OUT.exists():
        try:
            old = {it["id"]: it for it in json.loads(OUT.read_text("utf-8")).get("items", [])}
        except Exception:
            old = {}

    fresh, status = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(parse_feed, f): f for f in FEEDS}
        for fut in cf.as_completed(futures):
            f = futures[fut]
            try:
                its = [it for it in fut.result() if it["date"] and parse_date(it["date"]) >= cutoff]
                its.sort(key=lambda x: x["date"], reverse=True)
                fresh.extend(its[:MAX_PER_FEED])
                status.append({"source": f["name"], "ok": True, "count": len(its[:MAX_PER_FEED])})
                print(f"OK   {f['name']}: {len(its[:MAX_PER_FEED])}")
            except Exception as e:
                status.append({"source": f["name"], "ok": False, "count": 0})
                print(f"FAIL {f['name']}: {e}", file=sys.stderr)

    # объединяем со старыми, не теряя уже обработанные Claude поля
    merged = dict(old)
    for it in fresh:
        if it["id"] in old:
            keep = old[it["id"]]
            for k in ("image",):
                keep[k] = keep.get(k) or it.get(k)
            merged[it["id"]] = keep
        else:
            merged[it["id"]] = it

    items = [it for it in merged.values() if it.get("date") and parse_date(it["date"]) >= cutoff]
    items.sort(key=lambda x: x["date"], reverse=True)

    # дубли одной истории из разных лент
    seen, unique = set(), []
    for it in items:
        key = norm_title(it.get("title_orig") or it["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    items = unique[:MAX_ITEMS]

    # картинки: если в RSS нет — берём og:image со страницы
    need = [it for it in items if not it.get("image")][:80]
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for it, img in zip(need, ex.map(lambda x: og_image(x["link"]), need)):
            if img:
                it["image"] = img

    enrich_with_claude(items)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": now.isoformat(),
        "categories": [{"id": c[0], "label": c[1]} for c in CATEGORIES] + [{"id": "other", "label": "Разное"}],
        "sources": sorted(status, key=lambda s: s["source"]),
        "items": items,
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"\nГотово: {len(items)} новостей → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
