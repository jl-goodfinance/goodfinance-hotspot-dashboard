# KOL 貼文抓取（目前停用）

KOL 風向卡於 2026-08-16 從 dashboard 移除，這支抓取腳本與其 launchd 排程一併停用。

## 為何停用得這樣做

`launchctl bootout` 只對當次登入有效；只要 plist 還在 `~/Library/LaunchAgents/`，
重新登入後 launchd 會再次載入。因此把 plist 移到本目錄（副檔名加 `.disabled`）。

## 要恢復

```bash
cp scripts/com.goodfinance.kol-posts.plist.disabled \
   ~/Library/LaunchAgents/com.goodfinance.kol-posts.plist
launchctl enable gui/$(id -u)/com.goodfinance.kol-posts
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.goodfinance.kol-posts.plist
```

同時要在 build.py 恢復 `<!--KOL_CARD-->` 的 replace 呼叫，並在 template.html 加回該位置。
