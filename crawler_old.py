from datetime import datetime, timezone
from typing import List
from models import AnimeInfo


def mock_scrape_latest() -> List[AnimeInfo]:
    """
    模拟“抓取今日最新动漫资源列表”的逻辑。
    未来可以改成真实爬虫：
    1. 请求资源站（httpx.get）
    2. 用 lxml / bs4 解析标题、磁力链接、大小、清晰度、发布时间、来源
    3. 组装成 AnimeInfo 列表返回

    约定：
    - 必须返回 List[AnimeInfo]
    - 字段名、类型要和 models.AnimeInfo 对齐
    """

    # 我们用统一的时间戳，模拟“今天抓到的条目”
    now_iso = datetime.now(timezone.utc).isoformat()

    demo_data = [
        AnimeInfo(
            title="【10月新番】葬送的芙莉莲 - 第07集 简体内嵌",
            url="magnet:?xt=urn:btih:fakehash1111",
            size="1.23 GB",
            quality="1080p WEBRip",
            date=now_iso,
            source="某字幕组 · Nyaa",
        ),
        AnimeInfo(
            title="【BDRip】咒术回战 S2 全24集 完整打包",
            url="https://example.com/download/jujutsu_s2_batch.torrent",
            size="14.8 GB",
            quality="1080p BD x264 FLAC",
            date=now_iso,
            source="BDripClub",
        ),
        AnimeInfo(
            title="【剧场版】来自深渊 烈日の黄金郷 - 国语/日语 双音轨",
            url="magnet:?xt=urn:btih:fakehash2222",
            size="4.5 GB",
            quality="1080p BluRay",
            date=now_iso,
            source="动画冷门搬运工",
        ),
    ]

    return demo_data


import re
from datetime import datetime, timezone
from typing import List

import httpx
from bs4 import BeautifulSoup

from models import AnimeInfo


COMICAT_TODAY_URL = "https://comicat.org/today-1.html"


def _guess_size(text: str) -> str:
    """
    从文本里猜文件大小: 例如 "1.2 GB", "850 MB"
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s?(GB|GiB|MB|MiB)", text, flags=re.IGNORECASE)
    return m.group(0) if m else "未知大小"


def _guess_quality(text: str) -> str:
    """
    从文本里猜清晰度/版本: 优先找 2160p/4K, 再1080p,720p, etc.
    """
    q_patterns = [
        r"2160p|4K|4k|UHD",
        r"1080p|1080P|BDRip|BluRay|WEB[- ]?DL|WEB[- ]?Rip|WEBRip|WEBrip|BD\s?x?264|HEVC|x265",
        r"720p|720P",
    ]
    for pat in q_patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return "unknown"


def _guess_magnet_or_link(block) -> str:
    """
    从该条目的节点里尝试找到磁力链接或下载链接
    """
    # 1) magnet:?xt=urn:btih:...
    magnet = None
    for a in block.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("magnet:?xt=urn:btih:"):
            magnet = href
            break
    if magnet:
        return magnet

    # 2) .torrent / 外链下载
    for a in block.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".torrent") or "download" in href.lower():
            return href

    # fallback
    return ""


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def scrape_comicat_today() -> List[AnimeInfo]:
    """
    爬取 https://comicat.org/today-1.html 的最新资源列表。
    返回 AnimeInfo 数组，给 /api/scrape 用。

    策略：
    - 用桌面浏览器 UA 去请求，避免最简单的拒绝
    - 用 lxml/bs4 解析
    - 从常见块里提取每一行资源的信息
    - 为每条资源生成 AnimeInfo

    注意：
    comicat.org 在我们尝试打开 today-1.html 的时候，会跳转到一个
    `/public/html/start/` 防护页（很可能是 Cloudflare/自定义反爬或年龄验证页），
    也就是说直接无头请求可能也会被挡住。:contentReference[oaicite:3]{index=3}

    如果被挡住，httpx.get 拿到的不是真正的列表 HTML，这里就可能解析出 0 条，
    我们会上抛异常，前端就会收到 {"error": "..."}。
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(COMICAT_TODAY_URL)
        html = resp.text

    soup = BeautifulSoup(html)

    # 我们不知道站点具体 DOM，所以做多路尝试，把可能的“资源块”都抓出来：
    candidate_blocks = []

    # 常见：列表 <li class="..."> 或 <div class="item"> 或 表格行
    candidate_blocks.extend(soup.find_all("li"))
    candidate_blocks.extend(soup.find_all("div", class_=re.compile(r"(post|item|row)", re.I)))
    candidate_blocks.extend(soup.find_all("tr"))

    results: List[AnimeInfo] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for block in candidate_blocks:
        text_all = _clean_text(block.get_text(separator=" ", strip=True))
        if not text_all:
            continue

        # 我们要求这个块里面至少有一个 magnet: 或者明显是资源标题
        has_magnet = "magnet:?xt=urn:btih:" in text_all
        looks_like_anime = any(
            kw in text_all
            for kw in [
                "1080", "720", "BDRip", "BluRay", "WEB", "第", "集", "EP", "完结", "合集",
            ]
        )

        if not (has_magnet or looks_like_anime):
            continue

        # 猜 title
        #   很多站标题会是块里的第一句/第一行
        #   我们简单取前 120 字符当标题
        raw_title = text_all[:120]

        # 磁力 / 下载链接
        url = _guess_magnet_or_link(block) or ""

        # 文件大小
        size = _guess_size(text_all)

        # 清晰度
        quality = _guess_quality(text_all)

        # 组装 AnimeInfo
        info = AnimeInfo(
            title=raw_title,
            url=url or "N/A",
            size=size,
            quality=quality,
            date=now_iso,
            source="comicat.org",
        )

        # 去重：按 (title,url)
        sig = (info.title, info.url)
        if all((r.title, r.url) != sig for r in results):
            results.append(info)

    # 如果一个都没解析出来，说明(1)被反爬挡住了，或者(2)站点结构和我们假设完全不一样。
    if not results:
        raise RuntimeError("解析 comicat.org 页面失败，可能被反爬或页面结构变更")

    return results