#!/usr/bin/env python3
"""美好證券・節目選題熱點 Dashboard 產生器

抓取 Google Trends 台灣熱搜、鉅亨網分類新聞、證交所成交量排行、
KOL 訂閱數與動態，分類計算關鍵字熱度後，渲染 docs/index.html。

僅使用 Python 標準函式庫，供 GitHub Actions 排程執行。
"""
import http.cookiejar
import json
import re
import ssl
import statistics
import time
import html as htmlmod
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Taipei")
CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Accept-Language": "zh-TW,zh;q=0.9"}
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"

# ---------------------------------------------------------------- 分類規則
# 比對優先序：先命中者先歸類（避免「債券ETF」被歸到財富管理）
PRIORITY = ["固定收益", "融資", "財富管理", "總經", "股票交易"]
CATS = {
    "固定收益": ["債券", "美債", "公債", "公司債", "債息", "非投等", "非投資等級",
              "投等債", "債市", "債ETF", "固定收益"],
    "融資": ["融資", "融券", "當沖", "槓桿", "質押", "信用交易", "正2", "正二",
           "反1", "期貨", "選擇權", "權證", "保證金", "期交所"],
    "財富管理": ["高股息", "存股", "配息", "退休", "資產配置", "理財", "定期定額",
             "保險", "基金", "00878", "0056", "0050", "00929", "00919",
             "勞退", "勞保", "報稅", "息率", "ETF"],
    "總經": ["聯準會", "Fed", "升息", "降息", "通膨", "CPI", "關稅", "GDP", "非農",
           "就業", "央行", "匯率", "美元", "日元", "日圓", "利率", "油價", "原油",
           "歐元", "PMI", "貨幣", "川普", "經濟", "服務業", "景氣"],
    "股票交易": ["台股", "美股", "台積電", "輝達", "營收", "財報", "半導體", "記憶體",
             "AI", "晶片", "法說", "除息", "除權", "大盤", "加權指數", "櫃買",
             "韓股", "日股", "港股", "A股", "外資", "漲停", "跌停", "特斯拉",
             "蘋果", "個股", "EPS", "上市", "上櫃"],
}
CAT_STYLE = {  # 分類卡順序、熱度條配色 class
    "股票交易": "", "總經": "t", "財富管理": "o", "融資": "", "固定收益": "t",
}
CAT_NOTE = {"固定收益": "（含利率動向）"}
STOP_TAGS = {"TOP", "趨勢分析", "市場預估", "美國", "中國", "台灣", "日本",
             "韓國", "歐洲", "時事", "台灣時事"}

CNYES_CATS = ["headline", "tw_stock", "tw_macro", "wd_stock", "forex",
              "future", "etf", "fund"]

# KOL 名單池（Better Living 人物熱度）：
# 名稱 -> (YouTube 頻道路徑, 主題標籤, 標籤色, Google News 搜尋詞)
KOL_POOL = {
    "志祺七七": ("@shasha77", "泛知識 · 財經合作款", "", "志祺七七"),
    "Cheap": ("@cheapaoe", "歷史 · 時事評論", "", "YouTuber Cheap"),
    "柴鼠兄弟": ("channel/UC45i13dEfEVac2IEJT_Nr5Q", "ETF · 理財入門", "orange", "柴鼠兄弟"),
    "好葉": ("@betterleaf", "自我成長", "", "好葉 YouTuber"),
    "游庭皓的財經皓角": ("@yutinghaofinance", "每日盤勢 · 總經", "blue", "游庭皓"),
    "SHIN LI 李勛": ("@SHINLI", "小資理財 · 信用卡", "orange", "李勛 理財"),
    "阿格力": ("@agreedr", "存股 · 生活選股", "blue", "阿格力"),
    "Ms.Selena": ("@MsSelenaMrWayne", "被動收入 · 房產", "orange", "Ms.Selena"),
    "股癌 Gooaye": ("@Gooaye", "Podcast 台灣榜首", "green", "股癌"),
    "M觀點": ("@miulaviewpoint", "科技 · 商業", "", "M觀點 Miula"),
    "財報狗": ("@statementdog_official", "基本面 · Podcast", "blue", "財報狗"),
    "慢活夫妻": ("@GeorgeDewi", "理財生活", "", "慢活夫妻"),
}
GOOAYE_RSS = "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"

# 自訂監測關鍵字（Google Trends 相對熱度，彼此比較、峰值=100）
WATCH_KEYWORDS = ["開戶", "定期定額", "固定收益", "保本保息", "美好證券"]

# 每日監測的 YouTube 頻道：名稱 -> channelId
YT_CHANNELS = {
    "小Lin说": "UCilwQlk62k1z7aUEZPOB6yw",
    "游庭皓的財經皓角": "UC0lbAQVpenvfA2QqzsRtL_g",
    "Nicolas 楊應超": "UCXUP_aBLQBNFgLjvnrMTHtw",
    "工商時報": "UC9Ksf9o5OjzZWs2Jo8DC0Aw",
}
YT_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # YouTube 網頁版公開金鑰
YT_MAX_VIDEOS = 4       # 每頻道最多列幾支 24h 內影片
YT_MAX_COMMENTS = 5     # 每支影片最多列幾則讚數達標留言
YT_MIN_LIKES = 3

PTT_SKIP = re.compile(r"^\[公告\]|盤[中後]閒聊")
PTT_ENT = re.compile(
    r'<div class="r-ent">.*?<div class="nrec">(?:<span class="hl f\d+">)?([^<]*)'
    r'(?:</span>)?</div>.*?(?:<a href="(/bbs/Stock/[^"]+)">([^<]+)</a>).*?'
    r'<div class="date">([^<]+)</div>', re.S)

TREND_OFFTOPIC = {"體育": ["世界盃", "足球", "篮球", "籃球", "棒球", "NBA", "WNBA",
                        "lukaku", "球員", "中職"],
                  "娛樂": ["八點檔", "男星", "女星", "藝人", "演唱會", "專輯", "戲劇",
                        "告別式", "電影", "直播主", "實況"],
                  "教育": ["入學", "放榜", "考試", "學測", "分科"],
                  "社會": ["賭博", "詐騙", "車禍", "地震", "颱風", "命案", "火警"]}


def offtopic(text):
    for label, kws in TREND_OFFTOPIC.items():
        if any(k.lower() in text.lower() for k in kws):
            return label
    return None


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read()


def fetch_text(url, timeout=15):
    return fetch(url, timeout).decode("utf-8", "ignore")


def classify(text):
    for cat in PRIORITY:
        for kw in CATS[cat]:
            if kw.lower() in text.lower():
                return cat
    return None


def esc(s):
    return htmlmod.escape(str(s), quote=True)


# ---------------------------------------------------------------- 資料源
def get_google_trends():
    """Google Trends 台灣每日熱搜（全站）"""
    items = []
    try:
        root = ET.fromstring(fetch("https://trends.google.com.tw/trending/rss?geo=TW"))
        ns = {"ht": "https://trends.google.com/trending/rss"}
        for it in root.iter("item"):
            kw = it.findtext("title") or ""
            traffic = it.findtext("ht:approx_traffic", namespaces=ns) or ""
            news = [n.findtext("ht:news_item_title", namespaces=ns) or ""
                    for n in it.findall("ht:news_item", namespaces=ns)]
            ctx_text = kw + " " + " ".join(news)
            # 純數字代號（1101、00929、00631L…）直接視為股票
            if re.fullmatch(r"\d{4,6}[A-Za-z]?", kw.strip()):
                cat = "股票交易"
            else:
                cat = classify(ctx_text) or offtopic(ctx_text)
            if not cat:
                # RSS 沒附新聞時，回查 Google News 取得上下文再分類
                extra = " ".join(n["title"] for n in google_news(kw, 3))
                cat = classify(extra) or offtopic(extra)
            items.append({"kw": kw, "traffic": traffic, "news": news, "cat": cat})
    except Exception as e:
        print("trends error:", e)
    def tnum(t):
        m = re.match(r"([\d,]+)", t.get("traffic") or "0")
        return int(m.group(1).replace(",", "")) if m else 0
    items.sort(key=tnum, reverse=True)
    return items


def get_cnyes_news():
    """鉅亨網 8 個分類新聞（去重）"""
    seen, out = set(), []
    for cat in CNYES_CATS:
        try:
            d = json.loads(fetch_text(
                f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}?limit=60"))
            for n in d["items"]["data"]:
                if n["newsId"] in seen:
                    continue
                seen.add(n["newsId"])
                out.append({"id": n["newsId"], "title": n["title"],
                            "kw": n.get("keyword") or [], "at": n.get("publishAt") or 0})
        except Exception as e:
            print(f"cnyes {cat} error:", e)
    return out


def get_taiex():
    """大盤：指數線用 Yahoo ^TWII（3 個月），成交金額用證交所 FMTQIK。
    FMTQIK 僅當月可靠，故以 docs/taiex_amounts.json 逐日累積快取。"""
    now = datetime.now(TZ)

    # 1) 指數日線（Yahoo）
    days = []
    try:
        d = json.loads(fetch_text(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?range=1y&interval=1d"))
        r = d["chart"]["result"][0]
        closes = r["indicators"]["quote"][0]["close"]
        for ts, c in zip(r["timestamp"], closes):
            if c is None:
                continue
            dt = datetime.fromtimestamp(ts, TZ)
            days.append({"date": dt.strftime("%Y-%m-%d"), "index": round(c, 2)})
    except Exception as e:
        print("taiex yahoo error:", e)

    # 2) 成交金額（億）：當月 FMTQIK 併入累積快取
    cache_p = DOCS / "taiex_amounts.json"
    amounts = {}
    if cache_p.exists():
        try:
            amounts = json.loads(cache_p.read_text(encoding="utf-8"))
        except Exception:
            amounts = {}
    try:
        d = json.loads(fetch_text(
            f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={now:%Y%m}01&response=json"))
        for r in d.get("data", []):
            p = r[0].split("/")
            dt = datetime(int(p[0]) + 1911, int(p[1]), int(p[2]), tzinfo=TZ)
            # 防範端點偶發回傳預設月份的舊資料
            if (now - dt).days <= 40:
                amounts[dt.strftime("%Y-%m-%d")] = round(
                    float(r[2].replace(",", "")) / 1e8)
    except Exception as e:
        print("taiex fmtqik error:", e)
    if amounts:
        DOCS.mkdir(exist_ok=True)
        cache_p.write_text(json.dumps(amounts, ensure_ascii=False), encoding="utf-8")
    for day in days:
        day["amount"] = amounts.get(day["date"])

    # 3) 盤中即時指數（mis）
    rt = None
    try:
        q = json.loads(fetch_text(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0"))
        a = q["msgArray"][0]
        rt = {"z": float(a["z"]), "y": float(a["y"]), "t": a["t"], "d": a["d"]}
    except Exception as e:
        print("taiex realtime error:", e)
    # 只取今年（1/1 起）的交易日
    ytd = [d for d in days if d["date"] >= f"{now.year}-01-01"]
    return {"days": ytd or days[-260:], "rt": rt, "year": now.year}


def get_twse_top():
    """證交所成交量前 20（取前 10），失敗或空資料時退回上次成功的快取"""
    cache_p = DOCS / "twse_top.json"
    try:
        d = json.loads(fetch_text_retry(
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?response=json"))
        date = d.get("date", "")
        rows = [{"code": r[1], "name": r[2]} for r in d.get("data", [])[:10]]
        if rows:
            DOCS.mkdir(exist_ok=True)
            cache_p.write_text(json.dumps({"date": date, "rows": rows},
                                          ensure_ascii=False), encoding="utf-8")
            return date, rows
        raise ValueError("empty data")
    except Exception as e:
        print("twse error:", e)
        if cache_p.exists():
            try:
                c = json.loads(cache_p.read_text(encoding="utf-8"))
                return c["date"], c["rows"]
            except Exception:
                pass
        return "", []


def get_kols():
    """KOL 人物熱度：以近 7 天 Google News 聲量排序（訂閱數僅做同分排序）"""
    now = datetime.now(TZ)
    out = []
    for name, (path, topic, color, query) in KOL_POOL.items():
        subs = "—"
        try:
            page = fetch_text(f"https://www.youtube.com/{urllib.parse.quote(path)}")
            m = (re.search(r'"subscriberCountText":\{"[^}]*?simpleText":"([^"]+)"', page)
                 or re.search(r'"subscriberCountText":"([^"]+)"', page)
                 or re.search(r'"content":"([0-9.,]+\s*萬?\s*位訂閱者)"', page))
            if m:
                subs = m.group(1).replace("位訂閱者", "").strip()
        except Exception as e:
            print(f"kol {name} error:", e)
        news = google_news(query, limit=20)
        buzz = sum(1 for n in news
                   if n.get("dt") and (now - n["dt"]).days < 7)
        out.append({"name": name, "subs": subs, "topic": topic,
                    "color": color, "buzz": buzz})

    def snum(s):
        m = re.match(r"([\d.,]+)(萬?)", s["subs"])
        if not m:
            return -1
        v = float(m.group(1).replace(",", ""))
        return v * 10000 if m.group(2) else v
    out.sort(key=lambda k: (k["buzz"], snum(k)), reverse=True)
    return out[:10]


def get_gooaye():
    """股癌：最新集數 + 媒體報導他關注什麼"""
    ep, ep_date = "", ""
    try:
        root = ET.fromstring(fetch(GOOAYE_RSS))
        item = root.find("channel").find("item")
        ep = item.findtext("title") or ""
        pd = item.findtext("pubDate") or ""
        ep_date = pd[5:16]
    except Exception as e:
        print("gooaye rss error:", e)
    news = google_news("股癌", limit=3)
    return {"ep": ep, "ep_date": ep_date, "news": news}


def _yt_post(endpoint, body):
    req = urllib.request.Request(
        f"https://www.youtube.com/youtubei/v1/{endpoint}?key={YT_INNERTUBE_KEY}&prettyPrint=false",
        data=json.dumps(body).encode(),
        headers={**UA, "Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20, context=CTX).read().decode())


def _yt_comment_token(o):
    if isinstance(o, dict):
        if o.get("sectionIdentifier") == "comment-item-section":
            m = re.search(r'"token":\s*"([^"]+)"', json.dumps(o))
            if m:
                return m.group(1)
        for v in o.values():
            r = _yt_comment_token(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _yt_comment_token(v)
            if r:
                return r
    return None


def _likes_num(s):
    s = (s or "0").replace(",", "").strip()
    if "萬" in s:
        return float(s.replace("萬", "")) * 10000
    try:
        return int(s)
    except ValueError:
        return 0


def get_yt_comments(video_id):
    """單支影片的熱門留言（讚數 >= YT_MIN_LIKES），走網頁版 innertube，無需 API key"""
    ctxb = {"context": {"client": {"clientName": "WEB",
                                   "clientVersion": "2.20250701.01.00",
                                   "hl": "zh-TW", "gl": "TW"}}}
    r1 = _yt_post("next", {**ctxb, "videoId": video_id})
    tok = _yt_comment_token(r1)
    if not tok:
        return []
    r2 = _yt_post("next", {**ctxb, "continuation": tok})
    muts = r2.get("frameworkUpdates", {}).get("entityBatchUpdate", {}).get("mutations", [])
    out = []
    for mu in muts:
        p = (mu.get("payload") or {}).get("commentEntityPayload")
        if not p:
            continue
        txt = (p.get("properties") or {}).get("content", {}).get("content", "")
        author = (p.get("author") or {}).get("displayName", "")
        likes = ((p.get("toolbar") or {}).get("likeCountNotliked") or "0").strip()
        if _likes_num(likes) >= YT_MIN_LIKES:
            out.append({"likes": likes, "author": author,
                        "text": txt.replace("\n", " ")[:120]})
    out.sort(key=lambda c: -_likes_num(c["likes"]))
    return out[:YT_MAX_COMMENTS]


def yt_is_short(video_id):
    """Shorts 判斷：/shorts/{id} 若非 Shorts 會被轉址到 /watch"""
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            f"https://www.youtube.com/shorts/{video_id}", headers=UA),
            timeout=15, context=CTX)
        return "/shorts/" in r.geturl()
    except Exception as e:
        print(f"yt shorts check {video_id} error:", e)
        return False  # 判斷失敗時保留影片，寧可多列不漏列


def get_yt_monitor(hours=24):
    """指定頻道近 N 小時上片清單 + 高讚留言"""
    ns = {"a": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015"}
    now = datetime.now(TZ)
    result = []
    for name, cid in YT_CHANNELS.items():
        videos = []
        entries = []
        try:
            root = ET.fromstring(fetch(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
            for e in root.findall("a:entry", ns):
                pub = datetime.fromisoformat(
                    e.findtext("a:published", namespaces=ns)).astimezone(TZ)
                entries.append({"id": e.findtext("yt:videoId", namespaces=ns),
                                "title": e.findtext("a:title", namespaces=ns) or "",
                                "pub": pub})
            for ent in entries:
                if (now - ent["pub"]).total_seconds() > hours * 3600:
                    continue
                if yt_is_short(ent["id"]):
                    continue
                videos.append({"id": ent["id"], "title": ent["title"],
                               "at": ent["pub"].strftime("%m/%d %H:%M"),
                               "fallback": False, "comments": []})
                if len(videos) >= YT_MAX_VIDEOS:
                    break
            # 24 小時內無上片 → 退回最新一支非 Shorts 影片（標註非 24h 內）
            if not videos and entries:
                for ent in sorted(entries, key=lambda x: -x["pub"].timestamp()):
                    if not yt_is_short(ent["id"]):
                        videos.append({"id": ent["id"], "title": ent["title"],
                                       "at": ent["pub"].strftime("%m/%d %H:%M"),
                                       "fallback": True, "comments": []})
                        break
            for v in videos:
                try:
                    v["comments"] = get_yt_comments(v["id"])
                except Exception as e2:
                    print(f"yt comments {v['id']} error:", e2)
        except Exception as e:
            print(f"yt channel {name} error:", e)
        result.append({"channel": name, "videos": videos})
    return result


def get_watch_trends():
    """自訂關鍵字的 Google Trends 90 天日序列（非官方端點，含重試）。

    失敗時退回上一次成功的 docs/watch.json，讓卡片持續有資料。
    """
    cache = DOCS / "watch.json"
    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj),
            urllib.request.HTTPSHandler(context=CTX))
        opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
            ("Accept-Language", "zh-TW,zh;q=0.9"),
            ("Referer", "https://trends.google.com/trends/explore?geo=TW")]

        def get(url, tries=4):
            for i in range(tries):
                try:
                    return opener.open(url, timeout=20).read().decode("utf-8", "ignore")
                except urllib.error.HTTPError as e:
                    if e.code == 429 and i < tries - 1:
                        time.sleep(6 * (i + 1))
                    else:
                        raise

        get("https://trends.google.com/trends/?geo=TW")
        time.sleep(2)
        req = {"comparisonItem": [{"keyword": k, "geo": "TW", "time": "today 3-m"}
                                  for k in WATCH_KEYWORDS],
               "category": 0, "property": ""}
        raw = get("https://trends.google.com/trends/api/explore?hl=zh-TW&tz=-480&req="
                  + urllib.parse.quote(json.dumps(req, ensure_ascii=False)))
        data = json.loads(raw.split("\n", 1)[1] if raw.startswith(")]}") else raw)
        tl = next(w for w in data["widgets"] if w["id"] == "TIMESERIES")
        time.sleep(3)
        raw2 = get("https://trends.google.com/trends/api/widgetdata/multiline"
                   "?hl=zh-TW&tz=-480&req="
                   + urllib.parse.quote(json.dumps(tl["request"], ensure_ascii=False))
                   + "&token=" + tl["token"])
        d2 = json.loads(raw2.split("\n", 1)[1] if raw2.startswith(")]}") else raw2)
        pts = d2["default"]["timelineData"]
        out = {"fetched": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
               "series": {WATCH_KEYWORDS[i]: [p["value"][i] for p in pts]
                          for i in range(len(WATCH_KEYWORDS))},
               "last_date": pts[-1]["formattedTime"]}
        DOCS.mkdir(exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    except Exception as e:
        print("watch trends error:", e)
        if cache.exists():
            old = json.loads(cache.read_text(encoding="utf-8"))
            old["stale"] = True
            return old
        return None


def fetch_text_retry(url, tries=3, wait=3):
    for i in range(tries):
        try:
            return fetch_text(url)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(wait)


def get_ptt_hot(days=7, limit=10):
    """PTT Stock 板近一週爆文（推文數破百），排除公告與每日閒聊串"""
    out = []
    try:
        first = fetch_text_retry("https://www.ptt.cc/bbs/Stock/index.html")
        m = re.search(r'href="/bbs/Stock/index(\d+)\.html">&lsaquo;', first)
        if not m:
            return out
        latest = int(m.group(1)) + 1
        now = datetime.now(TZ)
        cutoff = now.timetuple().tm_yday - days
        for i in range(latest, latest - 12, -1):
            page = first if i == latest else fetch_text_retry(
                f"https://www.ptt.cc/bbs/Stock/index{i}.html")
            stop = False
            for ent in PTT_ENT.finditer(page):
                nrec, link, title, date = [g.strip() for g in ent.groups()]
                if nrec != "爆" or PTT_SKIP.search(title):
                    continue
                try:
                    mth, day = date.split("/")
                    d = datetime(now.year, int(mth), int(day), tzinfo=TZ)
                    if d.timetuple().tm_yday < cutoff:
                        stop = True
                        continue
                except ValueError:
                    pass
                out.append({"title": title, "url": "https://www.ptt.cc" + link,
                            "date": date})
            if stop:
                break
    except Exception as e:
        print("ptt error:", e)
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]


def google_news(query, limit=5):
    from email.utils import parsedate_to_datetime
    try:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
               + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        root = ET.fromstring(fetch(url))
        out = []
        for it in root.find("channel").findall("item")[:limit]:
            pd = it.findtext("pubDate") or ""
            try:
                dt = parsedate_to_datetime(pd).astimezone(TZ)
            except Exception:
                dt = None
            out.append({"title": it.findtext("title") or "",
                        "date": pd[5:16], "dt": dt})
        return out
    except Exception as e:
        print(f"gnews {query} error:", e)
        return []


# ---------------------------------------------------------------- 停留時間
STREAK_P = None  # 於 main 期間指向 DOCS/streak.json
CONTINUITY_HOURS = 36  # 距上次出現在此時數內視為連續在榜


def compute_streaks(keys_by_kind, now):
    """回傳 {kind: {key: 停留標籤}}，並更新 docs/streak.json 帳本。

    帳本格式：{kind: {key: {"first": "...", "last": "..."}}}
    連續在榜（上次出現距今 < CONTINUITY_HOURS）沿用 first，否則重新起算。
    """
    path = DOCS / "streak.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        ledger = {}
    fmt = "%Y-%m-%d %H:%M"
    now_s = now.strftime(fmt)
    labels, new_ledger = {}, {}
    for kind, keys in keys_by_kind.items():
        labels[kind] = {}
        new_ledger[kind] = {}
        old = ledger.get(kind, {})
        for key in keys:
            rec = old.get(key)
            first = now_s
            if rec:
                try:
                    last = datetime.strptime(rec["last"], fmt).replace(tzinfo=TZ)
                    if (now - last).total_seconds() < CONTINUITY_HOURS * 3600:
                        first = rec["first"]
                except Exception:
                    pass
            new_ledger[kind][key] = {"first": first, "last": now_s}
            days = (now.date() - datetime.strptime(first, fmt).date()).days + 1
            if days >= 2:
                labels[kind][key] = f"{days}天"
    DOCS.mkdir(exist_ok=True)
    path.write_text(json.dumps(new_ledger, ensure_ascii=False), encoding="utf-8")
    return labels


# ---------------------------------------------------------------- 彙整
def aggregate():
    now = datetime.now(TZ)
    trends = get_google_trends()
    news = get_cnyes_news()
    twse_date, twse = get_twse_top()
    kols = get_kols()
    gooaye = get_gooaye()
    banini = google_news("巴逆逆", limit=3)

    # 關鍵字熱度：鉅亨網標籤出現則數
    tag_count, tag_cat = Counter(), {}
    for n in news:
        for t in n["kw"]:
            if t in STOP_TAGS:
                continue
            tag_count[t] += 1
            if t not in tag_cat:
                tag_cat[t] = classify(t + " " + n["title"]) or "股票交易"
    kw_by_cat = defaultdict(list)
    for t, c in tag_count.most_common(300):
        if c < 2:
            continue
        kw_by_cat[tag_cat[t]].append({"kw": t, "n": c})

    # 各分類新聞（最新在前，取 10）
    news_by_cat = defaultdict(list)
    for n in sorted(news, key=lambda x: -x["at"]):
        c = classify(n["title"] + " " + " ".join(n["kw"]))
        if c:
            news_by_cat[c].append(n)
    cat_counts = {c: len(v) for c, v in news_by_cat.items()}

    fin_trends = [t for t in trends if t["cat"] in PRIORITY]

    # 停留時間：分類關鍵字、各分類新聞、熱搜關鍵字
    kw_keys = [f"{c}|{k['kw']}" for c in PRIORITY for k in kw_by_cat.get(c, [])[:7]]
    news_keys = [str(n["id"]) for c in PRIORITY for n in news_by_cat.get(c, [])[:10]]
    trend_keys = [t["kw"] for t in trends]
    stay = compute_streaks(
        {"kw": kw_keys, "news": news_keys, "trend": trend_keys}, now)

    return {
        "stay": stay,
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "trends": trends, "fin_trends": fin_trends,
        "kw_by_cat": {c: kw_by_cat[c][:7] for c in PRIORITY},
        "news_by_cat": {c: news_by_cat[c][:10] for c in PRIORITY},
        "cat_counts": cat_counts,
        "twse": twse, "twse_date": twse_date,
        "kols": kols, "gooaye": gooaye, "banini": banini,
        "ptt": get_ptt_hot(),
        "watch": get_watch_trends(),
        "social": load_social(),
        "taiex": get_taiex(),
        "yt": get_yt_monitor(),
    }


def load_social():
    """KOL FB 貼文（本機以 Chrome 擷取後存入 social.json，半自動更新）"""
    p = ROOT / "social.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print("social.json error:", e)
    return None


# ---------------------------------------------------------------- 渲染
def stay_badge(label):
    return f'<span class="stay">{esc(label)}</span>' if label else ""


def render_bars(kws, color_cls, stays=None, cat=""):
    if not kws:
        return '<div class="focus-why">今日尚無足量標籤，累積中。</div>'
    stays = stays or {}
    mx = max(k["n"] for k in kws)
    rows = []
    for k in kws:
        w = max(12, round(k["n"] / mx * 100))
        badge = stay_badge(stays.get(f'{cat}|{k["kw"]}'))
        rows.append(
            f'<div class="bar-row"><span class="name">{esc(k["kw"])}{badge}</span>'
            f'<div class="track"><div class="fill {color_cls}" style="width:{w}%"></div></div>'
            f'<span class="val">{k["n"]} 則</span></div>')
    return "\n".join(rows)


def render_news(items, stays=None):
    stays = stays or {}
    lis = []
    for n in items:
        t = esc(n["title"])
        badge = stay_badge(stays.get(str(n["id"])))
        lis.append(f'<li><span><a href="https://news.cnyes.com/news/id/{n["id"]}" '
                   f'target="_blank" rel="noopener">{t}</a>{badge}</span></li>')
    return "\n".join(lis) or "<li>今日暫無相關新聞</li>"


def render_cat_cards(data):
    cards = []
    spans = {"股票交易": "span6", "總經": "span6",
             "財富管理": "span4", "融資": "span4", "固定收益": "span4"}
    titles = {"融資": "融資／衍生性"}
    stay = data.get("stay", {})
    for cat in ["股票交易", "總經", "財富管理", "融資", "固定收益"]:
        n = data["cat_counts"].get(cat, 0)
        note = CAT_NOTE.get(cat, "")
        cards.append(f'''
      <div class="card {spans[cat]}">
        <div class="card-head"><h2>{titles.get(cat, cat)}</h2><span class="pill">今日 {n} 則{note}</span></div>
        {render_bars(data["kw_by_cat"].get(cat, []), CAT_STYLE[cat], stay.get("kw"), cat)}
        <ul class="news">
        {render_news(data["news_by_cat"].get(cat, []), stay.get("news"))}
        </ul>
      </div>''')
    return "\n".join(cards)


def render_focus(data):
    """三張焦點卡：最熱財經熱搜、其餘台股題材、KOL 風向"""
    ft = data["fin_trends"]
    top = ft[0] if ft else {"kw": "—", "traffic": "", "news": [""]}
    top_news = [n for n in (top.get("news") or []) if n]
    if not top_news and top["kw"] != "—":
        top_news = [n["title"].split(" - ")[0] for n in google_news(top["kw"], 2)]
    top_why = esc((top_news or ["—"])[0][:90])
    others = "、".join(esc(t["kw"]) for t in ft[1:5]) or "—"
    g = data["gooaye"]
    return f'''
      <div class="card span4">
        <span class="pill blue">今日最熱財經熱搜</span>
        <div class="kicker">Google 搜尋量</div>
        <div class="big">{esc(top["traffic"])}</div>
        <div class="focus-title" style="font-size:19px">{esc(top["kw"])}</div>
        <div class="focus-why">{top_why}</div>
      </div>

      <div class="card span8">
        <span class="pill violet">財經熱搜題材</span>
        <div class="kicker violet">同時竄進全站熱搜的財經字</div>
        <div class="focus-title" style="font-size:18px;margin-top:8px">{others}</div>
        <div class="focus-why">財經關鍵字擠進 Google 全站熱搜榜＝散戶都在查的「破圈」題材，適合當開場 hook。</div>
      </div>'''


def render_kol_card(data):
    """KOL 風向大卡：兩人各 5 則 FB 貼文 + 內容鋪陳觀察"""
    g = data["gooaye"]
    social = data.get("social")

    def post_lines(posts, n=5):
        lis = []
        for p in posts[:n]:
            txt = esc(p["text"][:110])
            if p.get("link"):
                txt = (f'<a href="{esc(p["link"])}" target="_blank" rel="noopener" '
                       f'style="color:inherit;text-decoration:none">{txt}</a>')
            lis.append(f'<div class="kol-post"><span class="kol-t">{esc(p["t"])}</span>{txt}</div>')
        return "".join(lis)

    if social:
        g_body = post_lines(social.get("gooaye", []))
        b_body = post_lines(social.get("banini", []))
        note = f'FB 貼文擷取於 {esc(social.get("updated", ""))}（自動更新）'
        insight = social.get("insight", "")
    else:
        g_body = ('<div class="kol-what">'
                  + "；".join(esc(x["title"].split(" - ")[0][:52]) for x in g["news"][:2])
                  + "</div>") if g["news"] else ""
        b_body = ('<div class="kol-what">'
                  + " · ".join(esc(x["title"].split(" - ")[0][:38]) for x in data["banini"][:3])
                  + "</div>") if data["banini"] else ""
        note, insight = "以媒體報導代理（無貼文擷取資料）", ""
    insight_html = (f'<div class="insight"><div class="insight-label">觀察</div>'
                    f'{esc(insight)}</div>') if insight else ""
    return f'''
      <div class="card" style="grid-column:span 12" id="kol">
        <div class="card-head"><h2>KOL 風向</h2><span class="pill orange">{note}</span></div>
        <div class="kol-grid">
          <div class="kol">
            <div class="kol-name">股癌 <span class="kol-meta">{esc(g["ep"])} · {esc(g["ep_date"])} · FB Gooaye</span></div>
            {g_body}
          </div>
          <div class="kol">
            <div class="kol-name">巴逆逆 <span class="kol-meta">FB 吃土鋁繩巴逆逆 · 反指標女神</span></div>
            {b_body}
          </div>
        </div>
        {insight_html}
      </div>'''


def render_kol_table(kols):
    rows = []
    for i, k in enumerate(kols, 1):
        hot = ' class="hot"' if i <= 3 else ""
        pill = f'pill {k["color"]}'.strip()
        rows.append(f'<tr{hot}><td><span class="rankball">{i}</span></td>'
                    f'<td style="font-weight:600">{esc(k["name"])}</td>'
                    f'<td class="num">{k["buzz"]} 則</td>'
                    f'<td><span class="{pill}">{esc(k["topic"])}</span></td></tr>')
    return "\n".join(rows)


def render_market_hero(taiex):
    """大盤全寬卡：指數折線 + 成交量柱 + 量能指標（台股慣例紅漲綠跌）"""
    days = taiex.get("days") or []
    if not days:
        return ""
    last = days[-1]
    rt = taiex.get("rt")
    # 以即時值為主（收盤後 mis 回傳的即為收盤值）
    idx = rt["z"] if rt else last["index"]
    prev = rt["y"] if rt else (days[-2]["index"] if len(days) > 1 else idx)
    chg = idx - prev
    pct = chg / prev * 100 if prev else 0
    up = chg >= 0
    ccls, sign = ("up", "▲") if up else ("down", "▼")
    known_amts = [d["amount"] for d in days if d.get("amount")]
    amt = known_amts[-1] if known_amts else 0
    avg_base = known_amts[-21:-1] or known_amts
    avg20 = statistics.mean(avg_base) if avg_base else 0
    vol_ratio = amt / avg20 if avg20 else 1
    n_avg = len(avg_base)
    lo = min(d["index"] for d in days)
    hi = max(d["index"] for d in days)
    pos = (idx - lo) / (hi - lo) * 100 if hi > lo else 50
    nd = len(days)
    year = taiex.get("year", "")
    ytd_pct = (idx / days[0]["index"] - 1) * 100 if days[0]["index"] else 0

    # SVG：上方指數線、下方量柱
    W, H, VH = 1000, 210, 52   # VH = 量柱區高
    LH = H - VH - 10
    n = len(days)
    xs = [i * W / (n - 1) for i in range(n)]
    pad = (hi - lo) * 0.08 or 1
    ylo, yhi = lo - pad, hi + pad

    def yv(v):
        return round(LH - (v - ylo) / (yhi - ylo) * LH, 1)
    line = " ".join(f"{round(x,1)},{yv(d['index'])}" for x, d in zip(xs, days))
    area = f"0,{LH} " + line + f" {W},{LH}"
    amx = max(known_amts) if known_amts else 1
    bw = max(2, round(W / n * 0.55, 1))
    bars = "".join(
        f'<rect x="{round(x - bw/2,1)}" y="{round(H - d["amount"]/amx*VH,1)}" '
        f'width="{bw}" height="{round(d["amount"]/amx*VH,1)}" rx="1" '
        f'fill="rgba(61,139,253,{0.55 if i == n-1 else 0.28})"/>'
        for i, (x, d) in enumerate(zip(xs, days)) if d.get("amount"))
    grid = "".join(
        f'<line x1="0" y1="{round(LH*f,1)}" x2="{W}" y2="{round(LH*f,1)}" '
        f'stroke="rgba(148,178,255,.08)"/>' for f in (0.25, 0.5, 0.75))
    t_label = f'{rt["t"][:5]} 更新' if rt else "收盤"

    # 軸標籤：Y 軸為指數（點）；X 軸以「月初垂直分隔線＋月份」標示
    def dfmt(s):
        p = s.split("-")
        return f"{int(p[1])}/{int(p[2])}" if len(p) == 3 else s
    ylabels = "".join(
        f'<span class="mk-ylab" style="top:{round(LH*f/H*100,1)}%">'
        f'{ylo + (1-f)*(yhi-ylo):,.0f}{" 點" if f == 0.25 else ""}</span>'
        for f in (0.25, 0.5, 0.75))
    vlines = []
    mlabels = [f'<span class="mk-mlab" style="left:0;transform:none">'
               f'{int(days[0]["date"][5:7])}月</span>']
    for i in range(1, n):
        m_now = days[i]["date"][5:7]
        if m_now != days[i-1]["date"][5:7]:
            x = xs[i]
            vlines.append(f'<line x1="{round(x,1)}" y1="0" x2="{round(x,1)}" y2="{H}" '
                          f'stroke="rgba(148,178,255,.18)" stroke-dasharray="3 4"/>')
            mlabels.append(f'<span class="mk-mlab" style="left:{round(x/W*100,2)}%">'
                           f'{int(m_now)}月</span>')
    vgrid = "".join(vlines)
    xlabels = f'<div class="mk-x">{"".join(mlabels)}</div>'
    return f'''
      <div class="card" style="grid-column:span 12">
        <div class="mk-top">
          <div>
            <div class="kicker" style="margin:0">台股加權指數 <span class="pill">{esc(dfmt(last["date"]))} · {esc(t_label)}</span></div>
            <div class="mk-idx">{idx:,.2f}<span class="mk-chg {ccls}">{sign} {abs(chg):,.2f}（{pct:+.2f}%）</span></div>
          </div>
          <div class="mk-stats">
            <div class="mk-stat"><span>成交金額</span><b>{amt/10000:,.2f} 兆</b></div>
            <div class="mk-stat"><span>量能（vs 近{n_avg}日均）</span><b class="{'up' if vol_ratio>1.15 else ''}">{vol_ratio:.2f}×</b></div>
            <div class="mk-stat"><span>今年以來</span><b class="{'up' if ytd_pct>=0 else 'down'}">{ytd_pct:+.1f}%</b></div>
            <div class="mk-stat"><span>年內區間位置</span><b>{pos:.0f}%</b></div>
            <div class="mk-stat"><span>年內高／低</span><b>{hi:,.0f} / {lo:,.0f}</b></div>
          </div>
        </div>
        <div class="mk-wrap">
          <svg class="mk-chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-label="加權指數近 {nd} 個交易日走勢與成交量">
            <defs>
              <linearGradient id="mkl" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stop-color="#3d8bfd"/><stop offset="1" stop-color="#7c6ff0"/>
              </linearGradient>
              <linearGradient id="mka" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="rgba(61,139,253,.28)"/><stop offset="1" stop-color="rgba(61,139,253,0)"/>
              </linearGradient>
            </defs>
            {grid}
            {vgrid}
            {bars}
            <polygon points="{area}" fill="url(#mka)"/>
            <polyline points="{line}" fill="none" stroke="url(#mkl)" stroke-width="2.2"
              style="filter:drop-shadow(0 0 6px rgba(61,139,253,.7))"/>
            <circle cx="{W}" cy="{yv(days[-1]['index'])}" r="4" fill="#7c6ff0"
              style="filter:drop-shadow(0 0 7px #7c6ff0)"/>
          </svg>
          {ylabels}
          <span class="mk-vlab">成交金額（億）· 峰值 {amx:,}</span>
        </div>
        {xlabels}
        <div class="focus-why">折線＝加權指數，單位：點（左側刻度）· 柱狀＝每日成交金額，單位：億元（今日 {amt:,} 億）· {year} 年初至今（{nd} 個交易日），虛線為月分隔 · 台股慣例紅漲綠跌</div>
      </div>'''


def render_watch(watch):
    if not watch:
        return ('<div class="focus-why">Google Trends 暫時無法取得，'
                '下次更新自動重試。</div>')
    panels = []
    for kw, vals in watch["series"].items():
        latest = vals[-1]
        avg4w = statistics.mean(vals[-28:]) if len(vals) >= 28 else statistics.mean(vals)
        if avg4w < 0.5 and latest < 1:
            arrow, cls, note = "—", "", "無搜尋量"
        elif latest > avg4w * 1.3:
            arrow, cls, note = "↑", "up", "竄升中"
        elif latest < avg4w * 0.7:
            arrow, cls, note = "↓", "down", "降溫"
        else:
            arrow, cls, note = "→", "", "持平"
        # sparkline：90 點 polyline，y 以全體最大值 100 為尺度
        w, h = 210, 44
        n = len(vals)
        mx = max(max(vals), 1)
        pts = " ".join(f"{round(i * w / (n - 1), 1)},{round(h - v / mx * (h - 6) - 2, 1)}"
                       for i, v in enumerate(vals))
        panels.append(f'''<div class="wk">
          <div class="wk-name">{esc(kw)}</div>
          <svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">
            <polyline points="{pts}" fill="none" stroke="url(#wg)" stroke-width="2"
              style="filter:drop-shadow(0 0 4px rgba(61,139,253,.7))"/>
          </svg>
          <div class="wk-val"><b>{latest}</b><span class="wk-avg">4週均 {round(avg4w, 1)}</span><span class="wk-delta {cls}">{arrow} {note}</span></div>
        </div>''')
    stale = '（快取資料，本次更新失敗）' if watch.get("stale") else ""
    return (f'<svg width="0" height="0" style="position:absolute"><defs>'
            f'<linearGradient id="wg" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="#3d8bfd"/><stop offset="1" stop-color="#7c6ff0"/>'
            f'</linearGradient></defs></svg>'
            f'<div class="watch">{"".join(panels)}</div>'
            f'<div class="focus-why" style="margin-top:12px">數值為五個關鍵字互相比較的相對熱度'
            f'（90 天內峰值＝100），資料至 {esc(watch.get("last_date", ""))}{stale}。</div>')


def render_yt(channels):
    blocks = []
    for ch in channels:
        vids = []
        fallback = any(v.get("fallback") for v in ch["videos"])
        for v in ch["videos"]:
            cms = "".join(
                f'<div class="yt-cm"><span class="yt-like">{esc(c["likes"])} 讚</span>'
                f'<span class="yt-author">{esc(c["author"])}</span>{esc(c["text"])}</div>'
                for c in v["comments"]) or '<div class="yt-cm none">尚無讚數 ≥ 3 的留言</div>'
            note = '<span class="pill" style="margin-left:8px">最新影片 · 非 24h 內</span>' \
                if v.get("fallback") else ""
            vids.append(
                f'<div class="yt-video">'
                f'<a href="https://www.youtube.com/watch?v={esc(v["id"])}" target="_blank" '
                f'rel="noopener" class="yt-title">{esc(v["title"])}</a>'
                f'<span class="yt-at">{esc(v["at"])}</span>{note}{cms}</div>')
        body = "".join(vids) or '<div class="yt-cm none">頻道暫無影片資料</div>'
        chip = "24h 無上片 · 顯示最新一支" if fallback else f'{len(ch["videos"])} 支'
        blocks.append(f'<div class="yt-ch"><div class="yt-ch-name">{esc(ch["channel"])}'
                      f'<span class="pill" style="margin-left:8px">{chip}</span></div>{body}</div>')
    return "".join(blocks)


def render_ptt(posts):
    rows = []
    for i, p in enumerate(posts, 1):
        hot = ' class="hot"' if i <= 3 else ""
        m = re.match(r"\[(\S+)\]\s*(.*)", p["title"])
        tag, title = (m.group(1), m.group(2)) if m else ("", p["title"])
        rows.append(f'<tr{hot}><td><span class="rankball">{i}</span></td>'
                    f'<td><a href="{esc(p["url"])}" target="_blank" rel="noopener" '
                    f'style="color:inherit;text-decoration:none;font-weight:600">{esc(title)}</a></td>'
                    f'<td><span class="pill">{esc(tag)}</span></td>'
                    f'<td class="num">{esc(p["date"])}</td></tr>')
    return "\n".join(rows) or '<tr><td colspan="4">本週暫無爆文</td></tr>'


def render_trends_table(trends, stays=None):
    stays = stays or {}
    pill_for = {"股票交易": "pill blue", "總經": "pill blue", "財富管理": "pill orange",
                "融資": "pill blue", "固定收益": "pill blue"}
    rows = []
    for i, t in enumerate(trends):
        cat = t["cat"] or "其他"
        cls = pill_for.get(cat, "pill")
        hot = ' class="hot"' if i < 3 and t["cat"] in PRIORITY else ""
        badge = stay_badge(stays.get(t["kw"]))
        rows.append(f'<tr{hot}><td style="font-weight:600">{esc(t["kw"])}{badge}</td>'
                    f'<td><span class="{cls}">{esc(cat)}</span></td>'
                    f'<td class="num">{esc(t["traffic"])}</td></tr>')
    return "\n".join(rows)


def render_twse(twse):
    rows = []
    for i, s in enumerate(twse, 1):
        hot = ' class="hot"' if i <= 3 else ""
        rows.append(f'<tr{hot}><td><span class="rankball">{i}</span></td>'
                    f'<td>{esc(s["code"])}</td>'
                    f'<td style="font-weight:600">{esc(s["name"])}</td></tr>')
    return "\n".join(rows)


def main():
    data = aggregate()
    DOCS.mkdir(exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    (DOCS / "data.json").write_text(payload, encoding="utf-8")
    # 歷史快照：累積供之後計算「熱度變化率」「連續在榜」
    hist = DOCS / "history"
    hist.mkdir(exist_ok=True)
    stamp = data["updated"].replace("-", "").replace(":", "").replace(" ", "-")
    (hist / f"{stamp}.json").write_text(payload, encoding="utf-8")
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (template
            .replace("<!--UPDATED-->", esc(data["updated"]))
            .replace("<!--MARKET_HERO-->", render_market_hero(data["taiex"]))
            .replace("<!--FOCUS_CARDS-->", render_focus(data))
            .replace("<!--CAT_CARDS-->", render_cat_cards(data))
            .replace("<!--KOL_ROWS-->", render_kol_table(data["kols"]))
            .replace("<!--TRENDS_ROWS-->", render_trends_table(
                data["trends"], data.get("stay", {}).get("trend")))
            .replace("<!--TWSE_ROWS-->", render_twse(data["twse"]))
            .replace("<!--TWSE_DATE-->", esc(
                f"{int(data['twse_date'][4:6])}/{int(data['twse_date'][6:8])}"
                if re.fullmatch(r"\d{8}", data["twse_date"]) else data["twse_date"]))
            .replace("<!--PTT_ROWS-->", render_ptt(data["ptt"]))
            .replace("<!--WATCH_PANELS-->", render_watch(data["watch"]))
            .replace("<!--YT_BLOCKS-->", render_yt(data["yt"]))
            .replace("<!--KOL_CARD-->", render_kol_card(data)))
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    print("rendered docs/index.html, updated", data["updated"])


if __name__ == "__main__":
    main()
