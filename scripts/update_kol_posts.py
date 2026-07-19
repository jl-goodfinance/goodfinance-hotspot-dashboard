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
  const timeRe = /^(\d+\s*(分鐘|小時|天|週|個月)|\d+[mhdwy]|昨天|[0-9]+月[0-9]+日)/;
  let time = null, link = null, r = m;
  for (let i = 0; i < 20 && r; i++) {
    r = r.parentElement;
    if (!r) break;
    if (!time) {
      const t = [...r.querySelectorAll('a')].map(a => a.innerText.trim()).find(x => timeRe.test(x));
      if (t) time = t;
    }
    if (!link) {
      const l = r.querySelector('a[href*=\"posts/\"], a[href*=\"/videos/\"], a[href*=\"/reel\"]');
      if (l) link = l.href;
    }
    if (time && link) break;
  }
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


def _osa_on_tab(url_key, action):
    """對所有視窗中網址含 url_key 的分頁執行動作（每步重新定位，
    避免使用者切換視窗或分頁休眠擴充改變分頁參照）"""
    return osa(f'''tell application "Google Chrome"
	repeat with w in every window
		repeat with t in every tab of w
			if URL of t contains "{url_key}" then
				{action}
			end if
		end repeat
	end repeat
	return "notfound"
end tell''')


def scrape(url):
    expand = ("[...document.querySelectorAll('div[role=\\\"button\\\"]')]"
              ".filter(b => /^(See more|顯示更多)$/.test(b.innerText.trim()))"
              ".slice(0,10).forEach(b => b.click()); 'ok'")
    js = EXTRACT_JS.strip().replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    url_key = url.rstrip("/").split("facebook.com/")[-1]
    # 獨立小視窗 + 立即縮到 Dock：不佔用使用者目前的視窗與焦點
    osa(f'''tell application "Google Chrome"
	make new window with properties {{bounds:{{60, 60, 620, 620}}}}
	set URL of active tab of front window to "{url}"
	set minimized of front window to true
end tell''')
    time.sleep(10)
    _osa_on_tab(url_key, 'execute t javascript "window.scrollTo(0, 4000)"')
    time.sleep(4)
    _osa_on_tab(url_key, f'execute t javascript "{expand}"')
    time.sleep(3)
    raw = _osa_on_tab(url_key, f'return execute t javascript "{js}"')
    if raw in ("notfound", "", "[]"):
        # 縮小狀態偶爾不載入內容：短暫展開該視窗重試一次
        try:
            osa(f'''tell application "Google Chrome"
	repeat with w in every window
		repeat with t in every tab of w
			if URL of t contains "{url_key}" then
				set minimized of w to false
				return "restored"
			end if
		end repeat
	end repeat
end tell''')
        except Exception:
            pass
        time.sleep(6)
        _osa_on_tab(url_key, 'execute t javascript "window.scrollTo(0, 4000)"')
        time.sleep(4)
        raw = _osa_on_tab(url_key, f'return execute t javascript "{js}"')
    try:
        # 刪除分頁會使 AppleScript 迭代索引失效，屬非致命錯誤
        _osa_on_tab(url_key, 'delete t\n\t\t\t\treturn "closed"')
    except Exception:
        pass
    if raw == "notfound":
        raise RuntimeError("找不到目標分頁（可能被休眠擴充回收）")
    posts = json.loads(raw)
    out = []
    fallback_t = f"{datetime.now(TZ).month}/{datetime.now(TZ).day} 擷取"
    for p in posts:
        text = (p.get("text") or "").strip()
        if not text or "置頂" in text or "斂財連結" in text:
            continue
        out.append({"t": p.get("time") or fallback_t,
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
