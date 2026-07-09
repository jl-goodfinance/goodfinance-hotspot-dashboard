#!/usr/bin/env python3
"""KOL FB 貼文自動更新（本機執行）

由 launchd 在登入後與每小時觸發。條件全部符合才動作：
  1. 網路可達 GitHub
  2. Google Chrome 正在執行（不主動啟動，避免打擾）
  3. 距離上次貼文擷取超過 MIN_INTERVAL_HOURS

流程：透過 Chrome 開 FB 粉專分頁 → 擷取貼文 → 關閉分頁 →
更新 social.json → git push → 觸發 GitHub Actions 立即重建頁面。
"""
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
REPO = Path(__file__).resolve().parent.parent
SOCIAL = REPO / "social.json"
MIN_INTERVAL_HOURS = 12
PAGES = {
    "gooaye": "https://www.facebook.com/Gooaye",
    "banini": "https://www.facebook.com/DieWithoutBang/",
}

EXTRACT_JS = r"""
JSON.stringify([...document.querySelectorAll('[data-ad-comet-preview=\"message\"]')].map(m => {
  const root = m.closest('[aria-posinset]') || m.closest('[role=\"article\"]') || (() => {
    let r = m; for (let i = 0; i < 14 && r; i++) { r = r.parentElement;
      if (r && r.querySelector('a[href*=\"posts/\"]')) return r; } return null; })();
  const link = root ? (root.querySelector('a[href*=\"posts/\"], a[href*=\"/videos/\"]') || {}).href : null;
  const time = root ? [...root.querySelectorAll('a')].map(a => a.innerText.trim())
    .find(x => /^(\d+\s*(分鐘|小時|天|週)|\d+[mhdw]|[0-9]+月[0-9]+日)/.test(x)) : null;
  return {time, link: link ? link.split('?')[0] : null,
          text: m.innerText.replace(/\n+/g, ' ').trim()};
}))
"""


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                       timeout=90)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def log(msg):
    print(f"[{datetime.now(TZ):%m-%d %H:%M}] {msg}", flush=True)


def online():
    try:
        socket.create_connection(("github.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


def chrome_running():
    r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True)
    return r.returncode == 0


def due():
    try:
        updated = json.loads(SOCIAL.read_text(encoding="utf-8")).get("updated", "")
        last = datetime.strptime(updated, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        return (datetime.now(TZ) - last).total_seconds() > MIN_INTERVAL_HOURS * 3600
    except Exception:
        return True


def scrape(url):
    osa(f'tell application "Google Chrome" to make new tab at end of tabs of '
        f'front window with properties {{URL:"{url}"}}')
    time.sleep(8)
    js_scroll = 'execute last tab of front window javascript "window.scrollTo(0, 4000)"'
    osa(f'tell application "Google Chrome" to {js_scroll}')
    time.sleep(4)
    expand = ("[...document.querySelectorAll('div[role=\\\"button\\\"]')]"
              ".filter(b => /^(See more|顯示更多)$/.test(b.innerText.trim()))"
              ".slice(0,10).forEach(b => b.click()); 'ok'")
    osa(f'tell application "Google Chrome" to execute last tab of front window '
        f'javascript "{expand}"')
    time.sleep(3)
    js = EXTRACT_JS.strip().replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    raw = osa(f'tell application "Google Chrome" to execute last tab of front window '
              f'javascript "{js}"')
    osa('tell application "Google Chrome" to close last tab of front window')
    posts = json.loads(raw)
    out = []
    for p in posts:
        text = (p.get("text") or "").strip()
        if not text or "置頂" in text or "斂財連結" in text:
            continue
        out.append({"t": p.get("time") or "最新",
                    "text": re.sub(r"\s*See (more|less)$", "", text)[:160],
                    "link": p.get("link")})
        if len(out) >= 5:
            break
    return out


def main():
    if not online():
        log("離線，跳過")
        return
    if not chrome_running():
        log("Chrome 未執行，跳過（下個小時再檢查）")
        return
    if not due():
        log(f"距上次更新未滿 {MIN_INTERVAL_HOURS} 小時，跳過")
        return
    data = json.loads(SOCIAL.read_text(encoding="utf-8"))
    changed = False
    for key, url in PAGES.items():
        try:
            posts = scrape(url)
            if posts:
                data[key] = posts
                changed = True
                log(f"{key}: 擷取 {len(posts)} 則")
            else:
                log(f"{key}: 未擷取到貼文，保留舊資料")
        except Exception as e:
            log(f"{key} 擷取失敗: {e}")
    if not changed:
        return
    data["updated"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    data["insight_stale"] = True  # 觀察由排程中的 Claude 任務重寫
    SOCIAL.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 先 commit 再 rebase 再 push（帶著未提交變更 rebase 會失敗）
    subprocess.run(["git", "-C", str(REPO), "add", "social.json"], check=True)
    r = subprocess.run(["git", "-C", str(REPO), "commit", "-m",
                        "auto-update KOL FB posts"], capture_output=True, text=True)
    if r.returncode != 0:
        log("無變更可提交")
        return
    subprocess.run(["git", "-C", str(REPO), "pull", "--rebase", "-X", "ours",
                    "origin", "main"], capture_output=True)
    p = subprocess.run(["git", "-C", str(REPO), "push"], capture_output=True)
    if p.returncode != 0:
        subprocess.run(["git", "-C", str(REPO), "pull", "--rebase", "-X", "ours",
                        "origin", "main"], capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "push"], check=True,
                       capture_output=True)
    subprocess.run(["gh", "workflow", "run", "update.yml",
                    "-R", "jl-goodfinance/goodfinance-hotspot-dashboard"],
                   capture_output=True)
    log("已推送並觸發雲端重建")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e}")
        sys.exit(1)
