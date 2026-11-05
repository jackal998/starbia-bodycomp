# -*- coding: utf-8 -*-
"""GitHub Actions：「新增量測」issue → 解析 QR → 自動檢查 → 寫入資料、產生報表、commit。

由 .github/workflows/measurement.yml 在 issue 開啟／編輯、或有人留言時執行。每次都從 issue
內容重新算一遍（不靠暫存狀態）：

  issue 開啟或編輯 → 下載所有照片、找出 STARBIA QR → 自動檢查（auto_checks）：
                     全部通過 → 直接寫入 raw/、重建 CSV、產生報表、commit、關閉 issue
                     有異常   → 留言（同一則會更新）列出原因，等使用者確認
  /confirm                 → 用目前顯示的時間寫入（使用者確認過，不再擋）
  /confirm 2026-01-15 18:30 → 指定時間並直接寫入
  /time 2026-01-15 18:30   → 改用這個時間，重新檢查並顯示（不寫入）
  /preview                 → 重新解析、檢查並顯示（不寫入）

時間的優先順序：最後一個 /time 留言 > 表單「實際量測時間」 > 照片 EXIF 拍攝時間 > issue 建立時間。
只處理 repo 擁有者／協作者的留言。照片網址從 API 的 body_html 取（private repo 的附件是有效期
幾分鐘的簽章網址），所以每次執行都重新下載。
"""
import io, json, os, random, re, subprocess, sys, time, unicodedata, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import starbia_qr as q

TZ = timezone(timedelta(hours=q.TZ_HOURS))
MARK = "<!-- starbia-intake -->"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
REPO_DIR = q.REPO_ROOT
DATA_DIR = os.path.relpath(q.ROOT, q.REPO_ROOT)          # 預設 body-comp
FORM_QR, FORM_TIME = "QR code 照片", "實際量測時間"
TZ_NAME = getattr(q, "TZ_NAME", "台灣時間")

# ---------------- GitHub API ----------------
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
TOKEN = os.environ.get("GITHUB_TOKEN", "")

def api(method, path, body=None, accept="application/vnd.github+json"):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": accept,
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "starbia-intake"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return json.loads(data) if data else None

def fetch(url):
    """附件下載：先不帶 token（簽章網址），失敗再帶 token。"""
    last = None
    for auth in (False, True):
        h = {"User-Agent": "starbia-intake"}
        if auth: h["Authorization"] = f"Bearer {TOKEN}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.read()
        except urllib.error.URLError as e:
            last = e
    raise last

# ---------------- 解析 issue ----------------
def norm_time(s):
    """各種寫法 → 'YYYY-MM-DD HH:MM'；看不懂就 None。"""
    s = unicodedata.normalize("NFKC", s or "")
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T]+(\d{1,2})[:](\d{2})", s)
    if not m: return None
    try:
        dt = datetime(*map(int, m.groups()))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")

COMMANDS = {"/confirm": "confirm", "/time": "preview", "/preview": "preview"}
HELP = ("可用的指令：`/confirm`（用目前顯示的時間寫入）、`/confirm 2026-01-15 18:30`（指定時間並寫入）、"
        "`/time 2026-01-15 18:30`（改時間，先不寫入）、`/preview`（重新讀取照片）。")

def parse_command(body):
    """留言 → (指令, 看不懂的指令)。寬鬆比對：全形「／」、大小寫、黏在指令後面的標點或時間
    （/Confirm。、/confirm2026-01-15 18:30）都接受。不是 / 開頭 → (None, None)；
    / 開頭但不認得（例如打錯成 /comfirm）→ (None, 第一個字)，好回覆提示而不是默默略過。"""
    s = unicodedata.normalize("NFKC", body or "").lstrip("\u200b\ufeff \t\r\n")
    if not s.startswith("/"): return None, None
    first = s.split()[0].lower()
    for k, v in COMMANDS.items():
        if first == k or (first.startswith(k) and not first[len(k)].isalpha()):
            return v, None
    return None, first

def form_field(body, label):
    m = re.search(rf"^###\s*{re.escape(label)}\s*$(.*?)(?=^###\s|\Z)", body or "", re.M | re.S)
    v = m.group(1).strip() if m else ""
    return "" if v == "_No response_" else v

def image_urls(html):
    return list(dict.fromkeys(re.findall(r'<img[^>]+src="([^"]+)"', html or "")))

def collect(issue, comments):
    """[(url, 來源)]：issue 內文＋受信任者留言裡的所有圖片。"""
    out = [(u, "issue") for u in image_urls(issue.get("body_html"))]
    for c in comments:
        if c.get("author_association") in TRUSTED and MARK not in (c.get("body") or ""):
            out += [(u, f"留言 {c['id']}") for u in image_urls(c.get("body_html"))]
    return out

def classify(downloads):
    """下載的照片分成 QR 照片（含 STARBIA QR）和其他（螢幕照片）。回傳 (rows, qr_imgs, other_imgs, exif_time, errors)"""
    from PIL import Image
    try:
        import pillow_heif; pillow_heif.register_heif_opener()
    except ImportError:
        pass
    rows, qr_imgs, others, exif, errors = [], [], [], None, []
    for url, data in downloads:
        try:
            im = Image.open(io.BytesIO(data)); im.load()
        except Exception as e:
            errors.append(f"打不開圖片（{type(e).__name__}）"); continue
        ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "HEIF": "heic", "GIF": "gif"}.get(im.format, "img")
        found = q.rows_from_image(im)
        if found:
            qr_imgs.append((data, ext))
            exif = exif or q.photo_time(im)
            rows += [r for r in found if r not in rows]
        else:
            others.append((data, ext))
    return rows, qr_imgs, others, exif, errors

def last_time_command(comments):
    t = None
    for c in comments:
        if c.get("author_association") not in TRUSTED: continue
        for line in (c.get("body") or "").splitlines():
            cmd, _ = parse_command(line)
            if cmd:
                t = norm_time(line) or t
    return t

def choose_time(issue, comments, exif):
    """回傳 (時間, 來源說明)。"""
    t = last_time_command(comments)
    if t: return t, "你的 `/time` 留言"
    f = form_field(issue.get("body"), FORM_TIME)
    if f:
        n = norm_time(f)
        if n: return n, "表單「實際量測時間」"
        return None, f"表單「實際量測時間」看不懂：`{f}`（格式 2026-01-15 18:30）"
    if exif: return exif, "照片的拍攝時間（EXIF）"
    created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")).astimezone(TZ)
    return created.strftime("%Y-%m-%d %H:%M"), "issue 建立時間（＝上傳時間）"

# ---------------- 自動判斷 ----------------
# 數值合理性：跟歷史比。預測值 = 趨勢線在最近一次量測的值；容許 = K × max(波動, 下限) + 下限 × 距上次的週數。
# 下限 ≈ 這類機器單次量測的誤差。用作者 14 次真實量測回測（每次只用它之前的資料）：最大只到容許的 54%，
# 不會誤擋；體重突然 +3 kg、骨骼肌 +2 kg、內臟脂肪 +15 cm² 都會被擋下。
PLAUSIBLE = [  # (欄位, 名稱, 單位, 小數, 下限)
    ("weight_kg", "體重", " kg", 1, 0.5), ("pbf_pct", "體脂率", "%", 2, 1.0), ("fat_kg", "脂肪質量", " kg", 2, 0.8),
    ("ffm_kg", "去脂質量", " kg", 2, 0.8), ("smm_kg", "骨骼肌質量", " kg", 2, 0.5), ("vfa_cm2", "內臟脂肪面積", " cm²", 2, 4.0),
    ("tbw_kg", "總體水分", " kg", 2, 0.6), ("bmd_gcm2", "骨密度", " g/cm²", 2, 0.04), ("phase_angle", "相位角", "°", 1, 0.2),
    ("ecw_ratio", "細胞外液比", "", 3, 0.004),
]
PLAUSIBLE_K, MIN_HISTORY = 3, 5

def history():
    return sorted(q.read_csv(q.CSV_PATH), key=lambda r: r["measured_at"])

def same_qr(raw):
    """這張 QR 已經記錄過 → 那筆的時間。"""
    i = q.qr_id(raw)
    return next((r["measured_at"] for r in history() if r["qr_id"] == i), None)

def identity_checks(raw, hist):
    if not hist:
        return [(True, "第一筆量測（沒有之前的資料可以比對）")]
    last = open(q.raw_path(hist[-1]["measured_at"]), encoding="utf-8").read().strip().split(",")
    diff = [label for i, label in ((q.ID, "ID"), (q.NAME, "姓名"), (q.DOB, "生日"), (q.SEX, "性別")) if raw[i] != last[i]]
    if diff:
        return [(False, f"QR 上的{'、'.join(diff)}跟之前的量測不一樣——是不是別人的 QR？")]
    out = [(True, "同一個人（ID、姓名、生日、性別都跟之前一樣）")]
    if abs(float(raw[q.HEIGHT]) - float(last[q.HEIGHT])) > 0.05:
        out.append((False, f"機器上輸入的身高跟上次不同（{last[q.HEIGHT]} → {raw[q.HEIGHT]} cm），BMI 等數值會跟著變"))
    return out

def plausibility_checks(rec, hist):
    import math, report
    if len(hist) < MIN_HISTORY:
        return [(True, f"之前只有 {len(hist)} 筆，累積 {MIN_HISTORY} 筆後才會比對數值是否異常（這次略過）")]
    T = lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M")
    t0 = T(hist[0]["measured_at"])
    xs = [(T(h["measured_at"]) - t0).total_seconds() / 86400 for h in hist]
    gap = max(0.0, (T(rec["measured_at"]) - T(hist[-1]["measured_at"])).total_seconds() / 86400)
    bad = []
    for k, name, unit, d, floor in PLAUSIBLE:
        ys = [float(h[k]) for h in hist]
        sd = math.sqrt(sum((y - report.local_linear(xs, ys, x)[0]) ** 2 for x, y in zip(xs, ys)) / (len(ys) - 1))
        pred = report.local_linear(xs, ys, xs[-1])[0]
        lim = PLAUSIBLE_K * max(sd, floor) + floor * gap / 7
        v = float(rec[k])
        if abs(v - pred) > lim:
            bad.append((False, f"{name} {report.fx(v, d)}{unit}：跟最近的趨勢（{report.fx(pred, d)}）差 "
                               f"{report.sg(v - pred, d)}，超過正常波動（±{report.fx(lim, d)}）"))
    return bad or [(True, f"{len(PLAUSIBLE)} 項主要數值都在正常波動內（跟之前 {len(hist)} 筆的趨勢比）")]

def auto_checks(ctx):
    """[(通過?, 說明)]；全部通過才自動寫入。只在有唯一一個 QR、時間可用時呼叫。"""
    raw, when, src = ctx["rows"][0], ctx["when"], ctx["src"]
    out = [(False, e) for e in ctx["errors"]]                     # 照片下載不到／打不開
    tc = q.machine_time_check(raw, when)
    if tc and tc[0]:
        out.append((True, f"量測時間 {when}（{src}）跟機器時鐘（{tc[2]}）吻合"))
    elif tc:
        out.append((False, f"量測時間 {when}（{src}）：{tc[1]}"))
    else:
        out.append((False, f"機器時間 `{raw[q.MACHINE_TIME]}` 讀不懂，無法驗證量測時間"))
    out += [(False, w) for w in ctx["warnings"]]                  # 時間在未來／比最新一筆還早
    warns = q.warnings_for(raw, when)                             # 欄位數、欄位關係、機器算的年齡
    out += [(False, w) for w in warns]
    if not warns:
        out.append((True, "QR 格式與 12 條欄位關係都正常"))
    hist = history()
    if any(h["measured_at"] == when for h in hist):
        out.append((False, f"{when} 已經有另一筆量測（不同的 QR）"))
    out += identity_checks(raw, hist)
    out += plausibility_checks(q.record(raw, when), hist)
    return out

def check_lines(checks):
    return [f"- {'✅' if ok else '❌'} {t}" for ok, t in sorted(checks, key=lambda c: c[0])]   # ❌ 排前面

# ---------------- 留言內容 ----------------
def preview_text(ctx, checks=None):
    rows, when, src = ctx["rows"], ctx["when"], ctx["src"]
    L = [MARK, "### ⚠ 需要你處理（還沒寫入）", ""]
    if not rows:
        L += [f"- ⚠ {e}" for e in ctx["errors"]]
        L += [f"❌ 在 {ctx['n_images']} 張照片裡找不到 STARBIA 報告的 QR code。",
              "請拍「掃描報告結果」畫面，讓 QR 佔畫面大部分、對焦清楚、不要反光，再編輯 issue 換照片（或留言補一張再留言 `/preview`）。"]
        return "\n".join(L)
    if len(rows) > 1:
        L += [f"❌ 找到 {len(rows)} 個不同的量測 QR（機器時間 " + "、".join(r[q.MACHINE_TIME] for r in rows) +
              "）。一個 issue 只放一次量測，請把其他的照片移到另一個 issue。"]
        return "\n".join(L)
    raw = rows[0]
    rec = q.record(raw, when or "?")
    if when and checks is None:
        checks = auto_checks(ctx)
    ok = bool(when) and all(c[0] for c in checks)
    L = [MARK, "### 🔎 檢查都通過（還沒寫入）" if ok else "### ⚠ 需要你確認（還沒寫入）", ""]
    if when:
        L += ["**自動檢查：**", *check_lines(checks), ""]
    L += [f"| 項目 | 值 |", "|:--|--:|",
          f"| **實際量測時間** | **{when or '—'}**（{src}） |",
          f"| 機器時間 | {raw[q.MACHINE_TIME]}（機器顯示的時間，多半是 UTC） |",
          f"| 體重 | {rec['weight_kg']} kg |", f"| 體脂率 | {float(rec['pbf_pct']):.2f} % |",
          f"| 骨骼肌質量 | {float(rec['smm_kg']):.2f} kg |", f"| 內臟脂肪面積 | {float(rec['vfa_cm2']):.2f} cm² |",
          f"| 骨密度 | {float(rec['bmd_gcm2']):.2f} g/cm² |",
          f"| 照片 | QR {ctx['n_qr']} 張、其他（螢幕）{ctx['n_other']} 張 |", ""]
    if not when:
        L += ["❌ 時間無法使用，請留言 `/confirm YYYY-MM-DD HH:MM`（改成正確的時間）。"]
    else:
        L += [f"**確認沒問題** → 在下面留言 `/confirm` 寫入　｜　**時間不對** → 留言 `/confirm 2026-01-15 18:30`"
              f"（改成正確的時間，{TZ_NAME}）"]
    return "\n".join(L)

# ---------------- 主流程 ----------------
def gather(repo, number):
    issue = api("GET", f"/repos/{repo}/issues/{number}", accept="application/vnd.github.full+json")
    comments = api("GET", f"/repos/{repo}/issues/{number}/comments?per_page=100", accept="application/vnd.github.full+json")
    urls = collect(issue, comments)
    downloads, errors = [], []
    for u, where in urls:
        try:
            downloads.append((u, fetch(u)))
        except Exception as e:
            errors.append(f"下載不到圖片（{where}：{type(e).__name__} {getattr(e, 'code', '')}）")
    rows, qr_imgs, others, exif, cerr = classify(downloads)
    when, src = choose_time(issue, comments, exif)
    warnings = []
    if when:
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        if when > now: warnings.append(f"時間 {when} 在未來（現在 {now}）")
        latest = max((r["measured_at"] for r in q.read_csv(q.CSV_PATH)), default=None)
        if latest and when < latest: warnings.append(f"時間比目前最新的量測（{latest}）還早 — 確定沒填錯？")
    return {"issue": issue, "comments": comments, "rows": rows, "qr_imgs": qr_imgs, "others": others,
            "when": when, "src": src, "errors": errors + cerr, "warnings": warnings,
            "n_images": len(urls), "n_qr": len(qr_imgs), "n_other": len(others)}

def upsert_comment(repo, number, comments, text):
    mine = [c for c in comments if MARK in (c.get("body") or "") and c.get("user", {}).get("type") == "Bot"]
    if mine:
        api("PATCH", f"/repos/{repo}/issues/comments/{mine[-1]['id']}", {"body": text})
    else:
        api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": text})

def git(*args):
    return subprocess.run(["git", "-C", REPO_DIR, *args], check=True, capture_output=True, text=True).stdout

def save_photos(when, qr_imgs, others):
    day, t = when.split()
    hhmm = t.replace(":", "")
    d = os.path.join(q.ROOT, "raw", day); os.makedirs(d, exist_ok=True)
    saved = []
    for i, (data, ext) in enumerate(qr_imgs, 1):
        name = f"qr_{hhmm}.{ext}" if i == 1 else f"qr_{hhmm}_{i}.{ext}"
        open(os.path.join(d, name), "wb").write(q.strip_gps(data)); saved.append(name)
    for i, (data, ext) in enumerate(others, 1):
        name = f"screen_{hhmm}_{i}.{ext}"
        open(os.path.join(d, name), "wb").write(q.strip_gps(data)); saved.append(name)
    return saved

def sync(branch):
    """工作目錄換成遠端 main 的最新版本。事件觸發時 checkout 的是「事件當下」的 commit，
    排隊或 re-run 後可能已經過時（例如前一個 issue 剛寫入），所以每次都重新同步。"""
    git("fetch", "--depth=1", "origin", branch)
    git("reset", "--hard", "FETCH_HEAD")
    git("clean", "-fdq", "--", DATA_DIR)       # 清掉上一次嘗試留下的未追蹤檔

def confirm(repo, number, ctx, branch, checks=None):
    """同步 → 寫入 → 產生報表 → commit → push。push 被拒（同時有別的 issue 寫入）就從最新的 main
    整個重做；產生的報表檔每次都會改，用 rebase 合併一定衝突，所以不合併、直接重算。"""
    raw, when = ctx["rows"][0], ctx["when"]
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    import report
    for attempt in range(5):
        sync(branch)
        dup = q.duplicate_of(raw, when)
        if dup:
            return "\n".join([MARK, f"### ✅ 這筆已經在資料裡（{dup}）", "",
                              "可能是之前的執行已經寫入。沒有重複寫入。"])
        q.add_measurement(raw, when)                   # 寫 raw csv 並重建 CSV
        saved = save_photos(when, ctx["qr_imgs"], ctx["others"])
        bad = report.main()
        git("add", "-A", DATA_DIR, "README.md")
        git("commit", "-m", f"量測 {when}（#{number}）")
        try:
            git("push", "origin", f"HEAD:{branch}")
            break
        except subprocess.CalledProcessError as e:
            print(f"push 被拒（第 {attempt + 1} 次），從最新的 {branch} 重做：{(e.stderr or '').strip()[-300:]}")
            if attempt == 4: raise
            time.sleep(2 ** attempt + random.random() * 2)
    rid = when.replace(" ", "_").replace(":", "")
    base = f"https://github.com/{repo}/blob/{branch}"
    return "\n".join([
        MARK, f"### ✅ 已自動寫入 {when}" if checks else f"### ✅ 已寫入 {when}", "",
        f"- 當日報表：[{rid}.md]({base}/{DATA_DIR}/reports/{rid}.md)",
        f"- 歷史報表：[history.md]({base}/{DATA_DIR}/history.md)",
        f"- 原始檔：`{DATA_DIR}/raw/{when[:10]}/`（{'、'.join(saved)}、qr_{rid[-4:]}.csv）",
        f"- commit：{git('rev-parse', '--short', 'HEAD').strip()}",
    ] + ([f"- ⚠ 螢幕驗證有 {bad} 項不一致，請看 VERIFY.md"] if bad else []) +
       (["", "**自動檢查都通過：**", *check_lines(checks), "",
         f"這筆有問題的話：到 `{DATA_DIR}/raw/{when[:10]}/` 刪掉檔名有 `{rid[-4:]}` 的檔案，報表會自動重新產生。"]
        if checks else []))

def main():
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    name = os.environ.get("GITHUB_EVENT_NAME")
    repo = event["repository"]["full_name"]
    branch = event["repository"]["default_branch"]
    issue = event["issue"]; number = issue["number"]
    if issue.get("pull_request") or f"### {FORM_QR}" not in (issue.get("body") or ""):
        print("不是「新增量測」issue，略過"); return
    cmd = "auto"                                       # issue 開啟／編輯：自動檢查，通過就寫入
    if name == "issue_comment":
        c = event["comment"]
        if c.get("author_association") not in TRUSTED or MARK in (c.get("body") or ""):
            print("不是受信任者的指令，略過"); return
        cmd, unknown = parse_command(c.get("body"))
        if unknown and issue.get("state") != "closed":
            api("POST", f"/repos/{repo}/issues/{number}/comments",
                {"body": f"⚠ 看不懂 `{unknown}`，這則留言沒有執行。\n\n{HELP}\n\n"
                         "打錯的話，直接再留一則新的，或編輯原本那則留言改正都可以。"})
            print(f"看不懂的指令 {unknown}，已回覆說明"); return
        if not cmd:
            print("不是指令，略過"); return
    if issue.get("state") == "closed":
        print("issue 已關閉，略過"); return

    sync(branch)                                       # 檢查與重複判斷都要看最新的資料
    ctx = gather(repo, number)
    rows, when = ctx["rows"], ctx["when"]
    close = lambda title=None: api("PATCH", f"/repos/{repo}/issues/{number}",
                                   {"state": "closed", "state_reason": "completed", **({"title": title} if title else {})})
    if len(rows) == 1 and cmd in ("auto", "confirm"):
        prev = same_qr(rows[0])
        if prev:                                       # 同一張 QR 再送一次：不用處理
            upsert_comment(repo, number, ctx["comments"],
                           "\n".join([MARK, f"### ✅ 這張 QR 已經記錄過（{prev}）", "", "沒有重複寫入，這個 issue 直接關閉。"]))
            close(); return
    if len(rows) == 1 and when and cmd in ("auto", "confirm"):
        checks = auto_checks(ctx)
        manual = cmd == "confirm" and not q.duplicate_of(rows[0], when)     # 使用者確認過：不再擋
        if manual or (cmd == "auto" and all(c[0] for c in checks)):
            text = confirm(repo, number, ctx, branch, checks if cmd == "auto" else None)
            upsert_comment(repo, number, ctx["comments"], text)
            close(f"量測 {when}"); return
        text = preview_text(ctx, checks)
    else:
        text = preview_text(ctx)
    if cmd == "confirm":
        text += "\n\n> ❌ `/confirm` 沒有執行：請先解決上面的問題。"
    upsert_comment(repo, number, ctx["comments"], text)

if __name__ == "__main__":
    main()
