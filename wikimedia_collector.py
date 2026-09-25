# -*- coding: utf-8 -*-
"""
Автоматизированный сборщик данных о предметной области
с ресурсов Wikimedia: Википедия, Викиданные, Викисловарь, Wikimedia Commons.
"""

import os
import re
import json
import time
import argparse
from urllib.parse import quote, unquote

import requests


# ---------------------------------------------------------------------------
# Базовые настройки
# ---------------------------------------------------------------------------
USER_AGENT = "WikimediaCollector/1.0 (educational project; contact: example@example.com)"
HEADERS = {"User-Agent": USER_AGENT}

WIKI_API = {
    "ru": "https://ru.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
    "de": "https://de.wikipedia.org/w/api.php",
}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKTIONARY_API = {
    "ru": "https://ru.wiktionary.org/w/api.php",
    "en": "https://en.wiktionary.org/w/api.php",
}
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

OUTPUT_DIR = "wikimedia_output"
GRAPH_MIN_NODES = 30


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def api_get(url: str, params: dict, retries: int = 3) -> dict:
    """GET-запрос к API с ретраями и корректным User-Agent."""
    params = dict(params)
    params.setdefault("format", "json")
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"[!] Ошибка запроса к {url}: {e}")
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Википедия
# ---------------------------------------------------------------------------
def search_wikipedia(query: str, lang: str = "ru", limit: int = 50) -> list:
    """Поиск статей в Википедии заданного языка."""
    data = api_get(WIKI_API[lang], {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srnamespace": 0,
    })
    hits = data.get("query", {}).get("search", [])
    return [h["title"] for h in hits]


def get_wikipedia_page(title: str, lang: str = "ru") -> dict:
    """Получить содержимое статьи + список внутренних ссылок."""
    data = api_get(WIKI_API[lang], {
        "action": "query",
        "prop": "extracts|links|images|info|pageimages",
        "titles": title,
        "explaintext": 1,
        "pllimit": "max",
        "plnamespace": 0,
        "imlimit": "max",
        "inprop": "url",
        "piprop": "original",
    })
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return {}
    page = next(iter(pages.values()))
    if "missing" in page:
        return {}
    return {
        "title": page.get("title"),
        "pageid": page.get("pageid"),
        "url": page.get("fullurl"),
        "extract": page.get("extract", ""),
        "links": [l["title"] for l in page.get("links", [])],
        "images": [i["title"] for i in page.get("images", [])],
        "image": (page.get("original") or {}).get("source"),
    }


def translate_title(title: str, src: str = "ru", dst: str = "en") -> str:
    """Автоперевод названия статьи через langlinks Википедии."""
    data = api_get(WIKI_API[src], {
        "action": "query",
        "titles": title,
        "prop": "langlinks",
        "lllang": dst,
        "redirects": 1,
    })
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return ""
    page = next(iter(pages.values()))
    ll = page.get("langlinks", [])
    return ll[0]["*"] if ll else ""


# ---------------------------------------------------------------------------
# 2. Викиданные
# ---------------------------------------------------------------------------
def get_wikidata_by_title(title: str, lang: str = "ru") -> dict:
    """Найти Q-ID сущности по названию статьи и получить её данные."""
    data = api_get(WIKIDATA_API, {
        "action": "wbgetentities",
        "sites": f"{lang}wiki",
        "titles": title,
        "props": "claims|labels|descriptions|sitelinks|aliases",
        "languages": "ru|en",
    })
    entities = data.get("entities", {})
    if not entities:
        return {}
    qid, entity = next(iter(entities.items()))
    if "missing" in entity:
        return {}
    return {"qid": qid, "entity": entity}


def get_wikidata_entity(qid: str) -> dict:
    data = api_get(WIKIDATA_API, {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims|labels|descriptions|sitelinks",
        "languages": "ru|en",
    })
    return data.get("entities", {}).get(qid, {})


# ---------------------------------------------------------------------------
# 3. Викисловарь
# ---------------------------------------------------------------------------
def get_wiktionary_definition(word: str, lang: str = "ru") -> dict:
    data = api_get(WIKTIONARY_API[lang], {
        "action": "query",
        "prop": "extracts",
        "titles": word,
        "explaintext": 1,
        "redirects": 1,
    })
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return {}
    page = next(iter(pages.values()))
    if "missing" in page:
        return {}
    return {
        "word": page.get("title"),
        "definition": page.get("extract", "")[:2000],
    }


# ---------------------------------------------------------------------------
# 4. Wikimedia Commons
# ---------------------------------------------------------------------------
def commons_search(query: str, limit: int = 20) -> list:
    """Поиск файлов на Викискладе."""
    data = api_get(COMMONS_API, {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": 6,          # File:
        "srlimit": limit,
    })
    hits = data.get("query", {}).get("search", [])
    return [h["title"] for h in hits]


def commons_file_info(file_title: str) -> dict:
    """Информация о файле (URL, описание, автор, лицензия)."""
    data = api_get(COMMONS_API, {
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime",
    })
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return {}
    page = next(iter(pages.values()))
    ii = page.get("imageinfo", [])
    if not ii:
        return {}
    info = ii[0]
    meta = info.get("extmetadata", {})
    return {
        "title": page.get("title"),
        "url": info.get("url"),
        "mime": info.get("mime"),
        "description": (meta.get("ImageDescription", {}) or {}).get("value", ""),
        "artist": (meta.get("Artist", {}) or {}).get("value", ""),
        "license": (meta.get("LicenseShortName", {}) or {}).get("value", ""),
    }


# ---------------------------------------------------------------------------
# 5. Изображение дня (Picture of the Day)
# ---------------------------------------------------------------------------
def get_picture_of_the_day() -> dict:
    """Получить POTD через шаблон на Викискладе."""
    data = api_get(COMMONS_API, {
        "action": "parse",
        "page": "Template:Potd",
        "prop": "text",
        "disablelimitreport": 1,
        "disableeditsection": 1,
    })
    text = data.get("parse", {}).get("text", {}).get("*", "")
    m = re.search(r'\[\[File:([^|\]]+)', text)
    if not m:
        # Резервный вариант — страница Template:Potd/текущая дата
        today = time.strftime("%Y-%m-%d")
        data = api_get(COMMONS_API, {
            "action": "parse",
            "page": f"Template:Potd/{today}",
            "prop": "text",
        })
        text = data.get("parse", {}).get("text", {}).get("*", "")
        m = re.search(r'\[\[File:([^|\]]+)', text)
    if not m:
        return {}
    file_title = "File:" + m.group(1).strip()
    info = commons_file_info(file_title)
    # Описание — вырезаем из HTML
    description = re.sub(r"<[^>]+>", "", info.get("description", "")).strip()
    return {
        "file_title": file_title,
        "url": info.get("url"),
        "description": description,
        "artist": re.sub(r"<[^>]+>", "", info.get("artist", "")).strip(),
        "license": info.get("license", ""),
    }


def save_picture_of_the_day(out_dir: str) -> dict:
    """Скачать POTD и сохранить рядом .txt с описанием."""
    potd = get_picture_of_the_day()
    if not potd or not potd.get("url"):
        print("[!] Не удалось получить изображение дня")
        return {}
    ensure_dir(out_dir)
    ext = os.path.splitext(potd["url"])[1] or ".jpg"
    base = os.path.join(out_dir, "picture_of_the_day")
    img_path = base + ext
    txt_path = base + ".txt"
    try:
        r = requests.get(potd["url"], headers=HEADERS, timeout=60)
        r.raise_for_status()
        with open(img_path, "wb") as f:
            f.write(r.content)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"Файл: {potd['file_title']}\n")
            f.write(f"Автор: {potd.get('artist','')}\n")
            f.write(f"Лицензия: {potd.get('license','')}\n\n")
            f.write(potd.get("description", ""))
        print(f"[+] Изображение дня сохранено: {img_path}")
    except Exception as e:
        print(f"[!] Ошибка сохранения POTD: {e}")
    return potd


# ---------------------------------------------------------------------------
# Основной сборщик
# ---------------------------------------------------------------------------
def collect_domain(query: str,
                   extra_langs: tuple = ("en", "de"),
                   max_articles: int = 35,
                   commons_limit: int = 15) -> dict:
    """
    Собрать данные о предметной области.
    Возвращает словарь, готовый к сериализации в JSON.
    """
    print(f"[*] Сбор данных по запросу: {query!r}")
    result = {
        "query": query,
        "articles": [],          # основные статьи
        "related": [],           # связанные статьи
        "wikidata": [],
        "wiktionary": [],
        "commons": [],
        "links": [],             # гиперссылки (рёбра графа)
    }

    # ---- 1. Основные статьи из русской Википедии ------------------------
    ru_titles = search_wikipedia(query, "ru", limit=max_articles)
    print(f"[+] Найдено статей в ru.wikipedia: {len(ru_titles)}")

    # Возьмём первые N как основные
    primary_titles = ru_titles[:max_articles]
    collected_titles = set()

    for title in primary_titles:
        page = get_wikipedia_page(title, "ru")
        if not page:
            continue
        collected_titles.add(page["title"])
        article = {
            "source": "ru.wikipedia",
            "title": page["title"],
            "url": page["url"],
            "extract": page["extract"][:4000],
            "image": page.get("image"),
            "images": page.get("images", [])[:20],
            "links": page.get("links", [])[:50],
        }
        result["articles"].append(article)

        # Рёбра графа
        for l in article["links"]:
            result["links"].append({"from": page["title"], "to": l})

        # ---- Викиданные по каждой основной статье --------------------
        wd = get_wikidata_by_title(page["title"], "ru")
        if wd:
            ent = wd["entity"]
            label = (ent.get("labels", {}).get("ru", {}) or {}).get("value", "")
            descr = (ent.get("descriptions", {}).get("ru", {}) or {}).get("value", "")
            sitelinks = ent.get("sitelinks", {})
            result["wikidata"].append({
                "qid": wd["qid"],
                "title": page["title"],
                "label": label,
                "description": descr,
                "sitelinks": {k: v.get("title") for k, v in sitelinks.items()},
            })

        # ---- Викисловарь по заголовку --------------------------------
        wt = get_wiktionary_definition(page["title"], "ru")
        if wt:
            result["wiktionary"].append(wt)

    # ---- 2. Связанные статьи (расширение графа до 30+) -------------------
    related_pool = []
    for art in result["articles"]:
        for l in art["links"]:
            if l not in collected_titles and l not in related_pool:
                related_pool.append(l)
    related_pool = related_pool[: max(0, GRAPH_MIN_NODES - len(collected_titles) + 5)]

    for title in related_pool:
        if len(result["articles"]) >= max_articles:
            break
        page = get_wikipedia_page(title, "ru")
        if not page:
            continue
        collected_titles.add(page["title"])
        result["related"].append({
            "source": "ru.wikipedia",
            "title": page["title"],
            "url": page["url"],
            "extract": page["extract"][:1500],
            "image": page.get("image"),
            "links": page.get("links", [])[:20],
        })
        for l in page.get("links", [])[:20]:
            result["links"].append({"from": page["title"], "to": l})

    # ---- 3. Иноязычные версии с автопереводом --------------------------
    for art in result["articles"][:10]:
        for lang in extra_langs:
            translated = translate_title(art["title"], "ru", lang)
            if not translated:
                continue
            page = get_wikipedia_page(translated, lang)
            if not page:
                continue
            result["articles"].append({
                "source": f"{lang}.wikipedia",
                "title": page["title"],
                "url": page["url"],
                "extract": page["extract"][:2000],
                "image": page.get("image"),
                "links": page.get("links", [])[:30],
                "translated_from": art["title"],
            })

    # ---- 4. Wikimedia Commons -----------------------------------------
    files = commons_search(query, limit=commons_limit)
    for f in files:
        info = commons_file_info(f)
        if info:
            result["commons"].append(info)

    # ---- 5. Статистика -------------------------------------------------
    result["stats"] = {
        "articles_total": len(result["articles"]) + len(result["related"]),
        "primary_articles": len(result["articles"]),
        "related_articles": len(result["related"]),
        "wikidata_entities": len(result["wikidata"]),
        "wiktionary_entries": len(result["wiktionary"]),
        "commons_files": len(result["commons"]),
        "edges": len(result["links"]),
    }
    return result


# ---------------------------------------------------------------------------
# Построение графа в формате DOT
# ---------------------------------------------------------------------------
def build_dot_graph(data: dict, min_nodes: int = GRAPH_MIN_NODES) -> str:
    """Построить граф связей в формате Graphviz DOT."""
    nodes = {}     # title -> (type, label)
    edges = set()

    # Главный узел — запрос
    root = data["query"]
    nodes[root] = ("root", root)

    for art in data["articles"] + data["related"]:
        t = art["title"]
        nodes[t] = ("article", t)
        edges.add((root, t))

    for link in data["links"]:
        src, dst = link["from"], link["to"]
        if src in nodes and dst in nodes and src != dst:
            edges.add((src, dst))

    # Дотягиваем до минимума
    if len(nodes) < min_nodes:
        for link in data["links"]:
            if len(nodes) >= min_nodes:
                break
            for t in (link["from"], link["to"]):
                if t not in nodes:
                    nodes[t] = ("article", t)
                    edges.add((root, t))

    def esc(s: str) -> str:
        return s.replace('"', '\\"')

    lines = [
        "digraph WikimediaGraph {",
        '  rankdir=LR;',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '  edge [color="#888888"];',
        f'  "{esc(root)}" [fillcolor="#ffd966", shape=ellipse, fontsize=14];',
    ]
    for t, (typ, label) in nodes.items():
        if typ == "root":
            continue
        color = "#cfe2f3" if typ == "article" else "#d9ead3"
        lines.append(f'  "{esc(t)}" [fillcolor="{color}"];')
    for s, d in sorted(edges):
        lines.append(f'  "{esc(s)}" -> "{esc(d)}";')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Сборщик данных Wikimedia по предметной области"
    )
    parser.add_argument("query", help="Запрос: личность, объект, событие и т.п.")
    parser.add_argument("--out", default=OUTPUT_DIR, help="Каталог вывода")
    parser.add_argument("--max", type=int, default=35, help="Максимум статей")
    parser.add_argument("--langs", default="en,de",
                        help="Доп. языки для автоперевода (через запятую)")
    parser.add_argument("--no-potd", action="store_true",
                        help="Не скачивать изображение дня")
    args = parser.parse_args()

    extra_langs = tuple(l.strip() for l in args.langs.split(",") if l.strip())

    ensure_dir(args.out)

    # 1) Сбор данных
    data = collect_domain(
        query=args.query,
        extra_langs=extra_langs,
        max_articles=args.max,
    )

    # 2) JSON
    json_path = os.path.join(args.out, "domain_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[+] JSON сохранён: {json_path}")
    print(f"    Статистика: {data['stats']}")

    # 3) Граф DOT
    dot_path = os.path.join(args.out, "graph.dot")
    with open(dot_path, "w", encoding="utf-8") as f:
        f.write(build_dot_graph(data, GRAPH_MIN_NODES))
    print(f"[+] Граф DOT сохранён: {dot_path}")

    # 4) Изображение дня
    if not args.no_potd:
        save_picture_of_the_day(args.out)


if __name__ == "__main__":
    main()