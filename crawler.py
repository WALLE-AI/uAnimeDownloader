# backend/crawler.py
import os
import re
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

from models import AnimeInfo


BASE_URL = "https://comicat.org/"
COMICAT_TODAY_URL = urljoin(BASE_URL, "today-1.html")
TZ = ZoneInfo("Asia/Taipei")  # 服务器时区可能不是台北，这里显式指定


# ------------------------
# 小工具
# ------------------------
def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _guess_size(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s?(GB|GiB|MB|MiB|KB)", text, flags=re.IGNORECASE)
    return m.group(0) if m else text or "未知大小"


def _guess_quality(text: str) -> str:
    q_patterns = [
        r"2160p|4K|UHD",
        r"1080p|BDRip|BluRay|WEB[- ]?DL|WEB[- ]?Rip|WEBRip|WEBrip|HEVC|x265|x264",
        r"720p",
    ]
    for pat in q_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return "unknown"


def _looks_like_captcha(html: str) -> bool:
    lower_html = html.lower()
    markers = ["i'm not a robot", "captcha", "visitor-test-form", "visitor_test"]
    return any(m in lower_html for m in markers)


def _parse_cn_time(cell_text: str, now: Optional[datetime] = None) -> datetime:
    """
    解析 '今天 21:41' / '昨天 08:12' / '2025-10-26 21:41' 等为带时区的 datetime
    """
    now = now or datetime.now(TZ)
    t = cell_text.strip()

    # 今天/昨天
    m = re.match(r"^(今天|昨天)\s+(\d{1,2}):(\d{2})$", t)
    if m:
        day_word, hh, mm = m.groups()
        base = now.date() if day_word == "今天" else (now - timedelta(days=1)).date()
        dt = datetime(base.year, base.month, base.day, int(hh), int(mm), tzinfo=TZ)
        return dt

    # YYYY-MM-DD HH:MM
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$", t)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        return datetime(y, mo, d, hh, mm, tzinfo=TZ)

    # 兜底：用 now
    return now


def _extract_magnet_from_detail(detail_html: str) -> str:
    """
    在详情页 HTML 里找 magnet 或 .torrent / download 链接
    """
    soup = BeautifulSoup(detail_html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("magnet:?xt=urn:btih:"):
            return href
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".torrent") or "download" in href.lower():
            return href
    return ""


def _fetch_detail_link(client: httpx.Client, url: str) -> str:
    """
    拉取详情页，尽力找磁力/下载链接，失败则返回原详情页 URL
    """
    try:
        r = client.get(url, timeout=6.0)
        if r.status_code == 200:
            magnet = _extract_magnet_from_detail(r.text)
            return magnet or url
    except Exception:
        pass
    return url


# ------------------------
# 核心解析：专用 DOM 选择器（高精度）
# ------------------------
def _parse_today_table(html: str, client_for_detail: Optional[httpx.Client] = None, max_detail: int = 12) -> List[AnimeInfo]:
    """
    解析 table#listTable tbody#data_list 中的条目，生成 AnimeInfo 列表。
    - 标题/链接：来自“标题”列的 <a>
    - 大小：来自“大小”列
    - 质量：从标题文本中猜
    - 日期：解析“发表时间”列（今天/昨天/yyyy-mm-dd）
    - URL：优先尝试详情页补抓磁力（最多补抓 max_detail 条；超过则直接用详情页 URL）
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.select_one("table#listTable tbody#data_list")
    if not table:
        return []

    rows = table.find_all("tr", recursive=False)
    now = datetime.now(TZ)

    results: List[AnimeInfo] = []
    detail_fetch_count = 0

    for tr in rows:
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue

        # 1) 发表时间
        date_text = _clean_text(tds[0].get_text())
        dt = _parse_cn_time(date_text, now=now)

        # 2) 类别（可用于源标签或过滤）
        # category = _clean_text(tds[1].get_text())  # 暂时不用；保留扩展

        # 3) 标题 + 详情页链接
        title_a = tds[2].find("a", href=True)
        if not title_a:
            continue
        title = _clean_text(title_a.get_text())
        detail_url = urljoin(BASE_URL, title_a["href"].strip())

        # 4) 大小
        size_text = _guess_size(_clean_text(tds[3].get_text()))

        # 5) 质量（从标题推断）
        quality = _guess_quality(title)

        # 6) URL：先用详情页链接；若允许、再补抓磁力
        final_url = detail_url
        if client_for_detail is not None and detail_fetch_count < max_detail:
            final_url = _fetch_detail_link(client_for_detail, detail_url)
            detail_fetch_count += 1

        results.append(
            AnimeInfo(
                title=title,
                url=final_url,
                size=size_text,
                quality=quality,
                date=dt.isoformat(),
                source="comicat.org",
            )
        )

    return results


# ------------------------
# 抓取入口
# ------------------------
def scrape_comicat_today() -> Tuple[List[AnimeInfo], str]:
    """
    实际抓取 https://comicat.org/today-1.html ，使用专用表格解析器。
    - 如设置 COMICAT_COOKIE，则携带 Cookie，减少被网关拦截的概率
    - 自动落盘 last_comicat_page.html 方便调试
    - 会对前 N 条详情页尝试补抓磁力/下载链接
    返回: (items, debug_msg)
    """
    cookie_header = os.environ.get("COMICAT_COOKIE", "").strip()

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
    if cookie_header:
        headers["Cookie"] = cookie_header

    # 保存原始 HTML 便于后续调试
    with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(COMICAT_TODAY_URL)
        html = resp.text

        try:
            with open("last_comicat_page.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            print("WARN: cannot write last_comicat_page.html:", e)
        # 如果抓到的是验证码页面，就尝试离线缓存
        if _looks_like_captcha(html):
            if os.path.exists("last_comicat_page.html"):
                with open("last_comicat_page.html", "r", encoding="utf-8") as f:
                    html = f.read()
                debug_msg = "线上页面被验证码拦截，使用本地缓存 last_comicat_page.html 解析"
            else:
                return [], "仍然是验证码/人机校验页面（没有本地缓存可用，请检查/更新 COMICAT_COOKIE）"
        else:
            # 我们成功拿到了真实页面 → 覆盖缓存
            try:
                with open("last_comicat_page.html", "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception as e:
                print("WARN: cannot write last_comicat_page.html:", e)
            debug_msg = "OK"

    # 用“表格专用解析器” +（前 12 条）详情页补抓磁力
    items = _parse_today_table(html, client_for_detail=client, max_detail=12)

    if not items:
        return [], "页面结构已加载，但没有从表格中解析到条目（可能页面改版或选择器不匹配）"

    return items[:5], debug_msg


# ------------------------
# 本地兜底
# ------------------------
def mock_scrape_latest() -> List[AnimeInfo]:
    now_iso = datetime.now(TZ).isoformat()
    return [
        AnimeInfo(
            title="【演示】数码宝贝 BEATBREAK - 04 [WebRip 1080p HEVC-10bit AAC][简繁内封字幕]",
            url="magnet:?xt=urn:btih:fakehash-123",
            size="567.6MB",
            quality="1080p HEVC",
            date=now_iso,
            source="mock.fallback",
        ),
        AnimeInfo(
            title="【演示】不擅吸血的吸血鬼 - 03 (Baha 1920x1080 AVC AAC MP4)",
            url=urljoin(BASE_URL, "show-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.html"),
            size="416.7MB",
            quality="1080p AVC",
            date=now_iso,
            source="mock.fallback",
        ),
        AnimeInfo(title='[桜都字幕組] 3年Z班銀八老師 / Gintama： 3-nen Z-gumi Ginpachi-sensei [03][1080p][繁體內嵌]', url='https://comicat.org/show-ed9716d5cadb66dabfcaa2f2c021c57a0b8281c1.html', size='624MB', quality='1080p', date='2025-10-26T12:30:00+08:00', source='comicat.org'),
        AnimeInfo(title='[桜都字幕组] 3年Z组银八先生 / Gintama： 3-nen Z-gumi Ginpachi-sensei [03][1080p][简体内嵌]', url='https://comicat.org/show-601fb5aad8615fc1d0e90f61467729cecb592d31.html', size='624.3MB', quality='1080p', date='2025-10-26T12:30:00+08:00', source='comicat.org'), 
        AnimeInfo(title='[雪飘工作室][キミとアイドルプリキュア♪/You and Idol Precure♪/与你同为 偶像光之美少女♪][720p][38（下周停播）][简体内嵌](检索:Q娃)', url='https://comicat.org/show-2b14be4e0b16ec04814193e35874b49f36229471.html', size='312.3MB', quality='720p', date='2025-10-26T11:53:00+08:00', source='comicat.org'),
        AnimeInfo(title='[黒ネズミたち] 数码宝贝 BEATBREAK / Digimon Beatbreak - 04 (CR 1920x1080 AVC AAC MKV)', url='https://comicat.org/show-b010399a18e5c8a105dca877edb15ed0dc95988f.html', size='927.2MB', quality='unknown', date='2025-10-26T11:40:00+08:00', source='comicat.org'), 
        AnimeInfo(title='[LoliHouse] 末世二轮之旅 / 终末摩托游 / Shuumatsu Touring - 04 [WebRip 1080p HEVC-10bit AAC][简繁内封字幕]', url='https://comicat.org/show-5e198eb8db4c1a2ae83aa3dbbe5ad3f5ea9411bf.html', size='819.9MB', quality='WebRip', date='2025-10-26T11:01:00+08:00', source='comicat.org'), 
        AnimeInfo(title='[XK SPIRITS][假面骑士ZEZTZ / KAMEN RIDER ZEZTZ][08][简日双语][1080P][WEBrip][MP4]（急招校对、时轴）', url='https://comicat.org/show-b1c76f32632891df6a97d94b106964b882c32863.html', size='664.9MB', quality='1080P', date='2025-10-26T11:13:00+08:00', source='comicat.org'),
    ]
