# 節目選題熱點 Dashboard

美好證券 YouTube 節目製作團隊的每日市場熱度總覽。每日 06:30 / 12:00 / 15:00（台北時間）由 GitHub Actions 自動更新，發布於 GitHub Pages。

## 資料源

| 來源 | 內容 | 用途 |
|---|---|---|
| Google Trends RSS（geo=TW） | 台灣每日熱搜榜 | 「破圈」題材偵測 |
| 鉅亨網 newslist API × 8 分類 | 新聞＋編輯關鍵字標籤 | 關鍵字熱度、五大分類新聞 |
| 證交所 MI_INDEX20 | 成交量前 20 | 散戶資金流向 |
| YouTube 頻道頁 | KOL 訂閱數 | 人物熱度 Top 10 |
| SoundOn Podcast RSS | 股癌最新集數 | KOL 風向 |
| Google News RSS | 股癌／巴逆逆 人物監測 | KOL 風向 |
| Apify Facebook Public Scraper | 股癌／巴逆逆各 5 則公開貼文 | KOL 風向（設定 token 後啟用） |

## 分類規則

新聞與關鍵字以字典比對歸入五大分類，優先序：**固定收益 → 融資 → 財富管理 → 總經 → 股票交易**。字典在 `build.py` 的 `CATS`，可直接編輯調整。

## 本地執行

```bash
python3 build.py   # 產出 docs/index.html 與 docs/data.json
```

僅使用 Python 標準函式庫（3.9+，需 zoneinfo），無需安裝套件。

## Facebook 貼文雲端更新

若 GitHub repository secret `APIFY_TOKEN` 已設定，三次例行更新會先透過
`lanky_quantifier/facebook-public-scraper` 抓取股癌與巴逆逆各 5 則公開貼文。
沒有 token、Actor 失敗或回傳空資料時，系統會保留最後一次成功資料，不影響其他區塊。

設定路徑：Repository → Settings → Secrets and variables → Actions →
New repository secret。名稱填 `APIFY_TOKEN`，值使用 Apify Console 的 API token。

## 調整

- **KOL 名單**：編輯 `build.py` 的 `KOL_POOL`
- **更新時間**：編輯 `.github/workflows/update.yml` 的 cron（UTC 時間）
- **版面樣式**：編輯 `template.html`
