#!/usr/bin/env python3
"""美好證券・節目選題熱點 Dashboard 產生器

抓取 Google Trends 台灣熱搜、鉅亨網分類新聞、證交所成交量排行、
KOL 訂閱數與動態，分類計算關鍵字熱度後，渲染 docs/index.html。

僅使用 Python 標準函式庫，供 GitHub Actions 排程執行。
"""
import json
import re
import ssl
import html as htmlmod
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

# KOL 名單池（Better Living 人物熱度）：名稱 -> (YouTube 頻道路徑, 主題標籤, 標籤色)
KOL_POOL = {
    "志祺七七": ("@shasha77", "泛知識 · 財經合作款", ""),
    "Cheap": ("@cheapaoe", "歷史 · 時事評論", ""),
    "柴鼠兄弟": ("channel/UC45i13dEfEVac2IEJT_Nr5Q", "ETF · 理財入門", "orange"),
    "好葉": ("@betterleaf", "自我成長", ""),
    "游庭皓的財經皓角": ("@yutinghaofinance", "每日盤勢 · 總經", "blue"),
    "SHIN LI 李勛": ("@SHINLI", "小資理財 · 信用卡", "orange"),
    "阿格力": ("@agreedr", "存股 · 生活選股", "blue"),
    "Ms.Selena": ("@MsSelenaMrWayne", "被動收入 · 房產", "orange"),
    "股癌 Gooaye": ("@Gooaye", "Podcast 台灣榜首", "green"),
    "M觀點": ("@miulaviewpoint", "科技 · 商業", ""),
    "財報狗": ("@statementdog_official", "基本面 · Podcast", "blue"),
    "慢活夫妻": ("@GeorgeDewi", "理財生活", ""),
}
GOOAYE_RSS = "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"

PTT_SKIP = re.compile(r"^\[公告\]|盤[中後]閒聊")
PTT_ENT = re.compile(
    r'<div class="r-ent">.*?<div class="nrec">(?:<span class="hl f\d+">)?([^<]*)'
    r'(?:</span>)?</div>.*?(?:<a href="(/bbs/Stock/[^"]+)">([^<]+)</a>).*?'
    r'<div class="date">([^<]+)</div>', re.S)

TREND_OFFTOPIC = {"體育": ["世界盃", "足球", "篮球", "籃球", "棒球", "NBA", "WNBA",
                        "lukaku", "球員", "中職"],
                  "娛樂": ["八點檔", "男星", "女星", "藝人", "演唱會", "專輯", "戲劇",
                        "告別式", "電影"],
                  "教育": ["入學", "放榜", "考試", "學測", "分科"]}


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
            cat = classify(ctx_text)
            if not cat:
                for label, kws in TREND_OFFTOPIC.items():
                    if any(k.lower() in ctx_text.lower() for k in kws):
                        cat = label
                        break
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


def get_twse_top():
    """證交所成交量前 20（取前 10）"""
    try:
        d = json.loads(fetch_text(
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?response=json"))
        date = d.get("date", "")
        return date, [{"code": r[1], "name": r[2]} for r in d.get("data", [])[:10]]
    except Exception as e:
        print("twse error:", e)
        return "", []


def get_kol_subs():
    """KOL YouTube 訂閱數即時抓取"""
    out = []
    for name, (path, topic, color) in KOL_POOL.items():
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
        out.append({"name": name, "subs": subs, "topic": topic, "color": color})

    def snum(s):
        m = re.match(r"([\d.,]+)(萬?)", s["subs"])
        if not m:
            return -1
        v = float(m.group(1).replace(",", ""))
        return v * 10000 if m.group(2) else v
    out.sort(key=snum, reverse=True)
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


def get_ptt_hot(days=7, limit=10):
    """PTT Stock 板近一週爆文（推文數破百），排除公告與每日閒聊串"""
    out = []
    try:
        first = fetch_text("https://www.ptt.cc/bbs/Stock/index.html")
        m = re.search(r'href="/bbs/Stock/index(\d+)\.html">&lsaquo;', first)
        if not m:
            return out
        latest = int(m.group(1)) + 1
        now = datetime.now(TZ)
        cutoff = now.timetuple().tm_yday - days
        for i in range(latest, latest - 12, -1):
            page = first if i == latest else fetch_text(
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
    try:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
               + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        root = ET.fromstring(fetch(url))
        out = []
        for it in root.find("channel").findall("item")[:limit]:
            out.append({"title": it.findtext("title") or "",
                        "date": (it.findtext("pubDate") or "")[5:16]})
        return out
    except Exception as e:
        print(f"gnews {query} error:", e)
        return []


# ---------------------------------------------------------------- 彙整
def aggregate():
    now = datetime.now(TZ)
    trends = get_google_trends()
    news = get_cnyes_news()
    twse_date, twse = get_twse_top()
    kols = get_kol_subs()
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
    return {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "trends": trends, "fin_trends": fin_trends,
        "kw_by_cat": {c: kw_by_cat[c][:7] for c in PRIORITY},
        "news_by_cat": {c: news_by_cat[c][:10] for c in PRIORITY},
        "cat_counts": cat_counts,
        "twse": twse, "twse_date": twse_date,
        "kols": kols, "gooaye": gooaye, "banini": banini,
        "ptt": get_ptt_hot(),
    }


# ---------------------------------------------------------------- 渲染
def render_bars(kws, color_cls):
    if not kws:
        return '<div class="focus-why">今日尚無足量標籤，累積中。</div>'
    mx = max(k["n"] for k in kws)
    rows = []
    for k in kws:
        w = max(12, round(k["n"] / mx * 100))
        rows.append(
            f'<div class="bar-row"><span class="name">{esc(k["kw"])}</span>'
            f'<div class="track"><div class="fill {color_cls}" style="width:{w}%"></div></div>'
            f'<span class="val">{k["n"]} 則</span></div>')
    return "\n".join(rows)


def render_news(items):
    lis = []
    for n in items:
        t = esc(n["title"])
        lis.append(f'<li><a href="https://news.cnyes.com/news/id/{n["id"]}" '
                   f'target="_blank" rel="noopener">{t}</a></li>')
    return "\n".join(lis) or "<li>今日暫無相關新聞</li>"


def render_cat_cards(data):
    cards = []
    spans = {"股票交易": "span6", "總經": "span6",
             "財富管理": "span4", "融資": "span4", "固定收益": "span4"}
    titles = {"融資": "融資／衍生性"}
    for cat in ["股票交易", "總經", "財富管理", "融資", "固定收益"]:
        n = data["cat_counts"].get(cat, 0)
        note = CAT_NOTE.get(cat, "")
        cards.append(f'''
      <div class="card {spans[cat]}">
        <div class="card-head"><h2>{titles.get(cat, cat)}</h2><span class="pill">今日 {n} 則{note}</span></div>
        {render_bars(data["kw_by_cat"].get(cat, []), CAT_STYLE[cat])}
        <ul class="news">
        {render_news(data["news_by_cat"].get(cat, []))}
        </ul>
      </div>''')
    return "\n".join(cards)


def render_focus(data):
    """三張焦點卡：最熱財經熱搜、其餘台股題材、KOL 風向"""
    ft = data["fin_trends"]
    top = ft[0] if ft else {"kw": "—", "traffic": "", "news": [""]}
    top_why = esc((top.get("news") or [""])[0][:90])
    others = "、".join(esc(t["kw"]) for t in ft[1:5]) or "—"
    g = data["gooaye"]
    g_news = "；".join(esc(x["title"].split(" - ")[0][:52]) for x in g["news"][:2]) or "—"
    b_news = " · ".join(esc(x["title"].split(" - ")[0][:38]) for x in data["banini"][:3]) or "—"
    return f'''
      <div class="card span4">
        <span class="pill blue">今日最熱 · Google 搜尋 {esc(top["traffic"])}</span>
        <div class="focus-title" style="font-size:19px;margin-top:12px">{esc(top["kw"])}</div>
        <svg class="spark" viewBox="0 0 300 56" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="sg" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stop-color="#3d8bfd"/><stop offset="1" stop-color="#7c6ff0"/>
            </linearGradient>
            <linearGradient id="sa" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="rgba(61,139,253,.35)"/><stop offset="1" stop-color="rgba(61,139,253,0)"/>
            </linearGradient>
          </defs>
          <path d="M0 48 L40 44 L80 46 L120 36 L160 40 L200 24 L240 28 L300 8" fill="none" stroke="url(#sg)" stroke-width="2.5" style="filter:drop-shadow(0 0 6px rgba(61,139,253,.8))"/>
          <path d="M0 48 L40 44 L80 46 L120 36 L160 40 L200 24 L240 28 L300 8 L300 56 L0 56 Z" fill="url(#sa)"/>
          <circle cx="300" cy="8" r="3.5" fill="#7c6ff0" style="filter:drop-shadow(0 0 6px #7c6ff0)"/>
        </svg>
        <div class="focus-why">{top_why}</div>
      </div>

      <div class="card span4">
        <span class="pill violet">財經熱搜題材</span>
        <div class="kicker">同時竄進全站熱搜的財經字</div>
        <div class="focus-title" style="font-size:18px;margin-top:8px">{others}</div>
        <div class="focus-why">財經關鍵字擠進 Google 全站熱搜榜＝散戶都在查的「破圈」題材，適合當開場 hook。</div>
      </div>

      <div class="card span4">
        <span class="pill orange">KOL 風向</span>
        <div class="kol">
          <div class="kol-name">股癌 <span class="kol-meta">{esc(g["ep"])} · {esc(g["ep_date"])}</span></div>
          <div class="kol-what">{g_news}</div>
        </div>
        <div class="kol">
          <div class="kol-name">巴逆逆 <span class="kol-meta">FB 吃土鋁繩巴逆逆 · 反指標女神</span></div>
          <div class="kol-what">{b_news}</div>
        </div>
      </div>'''


def render_kol_table(kols):
    rows = []
    for i, k in enumerate(kols, 1):
        hot = ' class="hot"' if i <= 3 else ""
        pill = f'pill {k["color"]}'.strip()
        rows.append(f'<tr{hot}><td><span class="rankball">{i}</span></td>'
                    f'<td style="font-weight:600">{esc(k["name"])}</td>'
                    f'<td><span class="{pill}">{esc(k["topic"])}</span></td></tr>')
    return "\n".join(rows)


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


def render_trends_table(trends):
    pill_for = {"股票交易": "pill blue", "總經": "pill blue", "財富管理": "pill orange",
                "融資": "pill blue", "固定收益": "pill blue"}
    rows = []
    for i, t in enumerate(trends):
        cat = t["cat"] or "其他"
        cls = pill_for.get(cat, "pill")
        hot = ' class="hot"' if i < 3 and t["cat"] in PRIORITY else ""
        rows.append(f'<tr{hot}><td style="font-weight:600">{esc(t["kw"])}</td>'
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
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    (DOCS / "data.json").write_text(payload, encoding="utf-8")
    # 歷史快照：累積供之後計算「熱度變化率」「連續在榜」
    hist = DOCS / "history"
    hist.mkdir(exist_ok=True)
    stamp = data["updated"].replace("-", "").replace(":", "").replace(" ", "-")
    (hist / f"{stamp}.json").write_text(payload, encoding="utf-8")
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    page = (template
            .replace("<!--UPDATED-->", esc(data["updated"]))
            .replace("<!--FOCUS_CARDS-->", render_focus(data))
            .replace("<!--CAT_CARDS-->", render_cat_cards(data))
            .replace("<!--KOL_ROWS-->", render_kol_table(data["kols"]))
            .replace("<!--TRENDS_ROWS-->", render_trends_table(data["trends"]))
            .replace("<!--TWSE_ROWS-->", render_twse(data["twse"]))
            .replace("<!--TWSE_DATE-->", esc(data["twse_date"]))
            .replace("<!--PTT_ROWS-->", render_ptt(data["ptt"])))
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    print("rendered docs/index.html, updated", data["updated"])


if __name__ == "__main__":
    main()
