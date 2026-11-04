# -*- coding: utf-8 -*-
"""產生所有報表（GitHub 上直接看的 Markdown + SVG），一律由 raw/ 與 CSV 重算：

  python scripts/report.py

  body-comp/reports/<日期_HHMM>.md      當日報表：還原機器報告（表格、評估長條、骨密度、
                                         評分、各肢段、建議）＋與上次比較＋可信度說明
  body-comp/history.md                  歷史報表：每次量測一列、各指標趨勢圖、完整數值表
  body-comp/charts/<指標>.svg           趨勢圖（history.md 與 README 共用）
  README.md 「最新」區塊                 <!-- latest:start --> … <!-- latest:end --> 之間自動改寫
  body-comp/dashboard.html              同樣內容的單檔 HTML（選用，下載後離線開）
  body-comp/raw/<日期>/VERIFY.md        有螢幕抄錄（screen_values.csv）的那天：逐項比對結果

圖都是 SVG（含深色模式），GitHub 的 Markdown 會直接顯示。
"""
import os, re, shutil, sys
from decimal import Decimal, ROUND_HALF_UP
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import starbia_qr as q

ROOT = q.ROOT
REPO = q.REPO_ROOT
REPORTS = os.path.join(ROOT, "reports")
CHARTS = os.path.join(ROOT, "charts")
HISTORY = os.path.join(ROOT, "history.md")
DASHBOARD = os.path.join(ROOT, "dashboard.html")
README = os.path.join(REPO, "README.md")   # 只有預設資料夾（body-comp/）才改寫 README 的「最新」區塊

# ---------------- 數字 ----------------
def fx(v, d=1):
    """依小數位數顯示。QR 只到小數兩位：剛好落在進位邊界（例如 x.x5 要顯示一位）時，機器是用
    內部更精確的值決定進位方向（螢幕上有的進位、有的捨去），無從得知 → 多顯示一位，不猜。"""
    if v is None: return "—"
    x = Decimal(str(v))
    q_ = Decimal(1).scaleb(-d)
    if (x / q_) % 1 == Decimal("0.5") or (x / q_) % 1 == Decimal("-0.5"):
        return str(x.quantize(q_ / 10))
    return str(x.quantize(q_, rounding=ROUND_HALF_UP))

def sg(v, d=2):
    s = fx(abs(v), d)
    return ("+" if v > 0 and float(s) else "−" if v < 0 and float(s) else "") + s

# ---------------- 資料模型 ----------------
def load():
    rows = sorted(q.read_csv(q.CSV_PATH), key=lambda r: r["measured_at"])
    general, per = {}, {}
    for r in q.read_csv(q.RANGES_PATH):
        rng = (float(r["low"]), float(r["high"]))
        (per.setdefault(r["measured_at"], {}) if r["measured_at"] else general)[r["metric"]] = rng
    import verify_screen
    checked = verify_screen.counts_by_measurement()
    out = []
    for r in rows:
        at = r["measured_at"]
        raw = open(q.raw_path(at), encoding="utf-8").read().strip().split(",")
        v = {k: float(r[k]) for k in list(q.FIELDS) + ["ecw_ratio"]}
        segs = {}
        for name, parts in q.SEGMENTS.items():
            segs[name] = {f"{k}_{s}": float(raw[i]) for k, (ki, si) in parts.items() for s, i in (("kg", ki), ("score", si))}
        w = v["weight_kg"]
        vfa_level = v["vfa_cm2"] / 10
        d = {
            "pct_bmc": v["bmc_kg"] / w * 100, "pct_muscle": v["muscle_kg"] / w * 100,
            "pct_storage_fat": v["storage_fat_kg"] / w * 100, "pct_essential_fat": v["essential_fat_kg"] / w * 100,
            "vfa_level": vfa_level,
        }
        day = at[:10]
        out.append({
            "id": at.replace(" ", "_").replace(":", ""),
            "measured_at": at, "day": day, "machine_time": r["machine_time"],
            "machine_age": int(float(r["machine_age"])), "real_age": q.age_on(raw[q.DOB], day),
            "person": {"id": raw[q.ID], "name": raw[q.NAME], "height_cm": float(raw[q.HEIGHT]),
                       "sex": "男性" if raw[q.SEX] == "1" else "女性"},
            "v": v, "d": d, "ranges": {**general, **per.get(at, {})},
            "scores": {k: (float(raw[i]), conf, why) for k, (i, conf, why) in q.SCORES.items()},
            "segments": segs, "qr_id": r["qr_id"],
            "screen_checked": checked.get(at, 0),                 # 被螢幕照片核對過的項目數
            "screen_verified": checked.get(at, 0) >= 50,          # 整份報告都有螢幕照片
            "time_check": q.machine_time_check(raw, at),
        })
    return out

def vfa_level_text(x):
    """機器顯示整數等級；面積 ÷ 10 的小數 < .5 時四捨五入與無條件捨去結果相同，才確定。"""
    lo, hi = int(x), int(x) + 1
    return (str(lo), True) if x - lo < 0.5 else (f"{lo}–{hi}", False)

# 狀態（顏色只是輔助，一律配文字）：本 repo 自訂的分級，機器只用漸層色
def status(score):
    if score >= 0: return "good", "良好"
    if score >= -1: return "good", "尚可"
    if score >= -2: return "warning", "注意"
    if score >= -2.5: return "serious", "警示"
    return "critical", "風險高"

# ---------------- SVG ----------------
FONT = '-apple-system, "Segoe UI", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif'
STYLE = f"""<style>
  .bg {{ fill: #fcfcfb; }} text {{ font-family: {FONT}; fill: #52514e; font-size: 12px; }}
  .ink {{ fill: #0b0b0b; }} .muted {{ fill: #898781; }} .b {{ font-weight: 600; }}
  .grid {{ stroke: #e1e0d9; stroke-width: 1; }} .axis {{ stroke: #c3c2b7; stroke-width: 1; }} .track {{ fill: #f0efec; }}
  .s1 {{ fill: #2a78d6; }} .s2 {{ fill: #eb6834; }} .s3 {{ fill: #1baf7a; }} .s4 {{ fill: #eda100; }}
  .good {{ fill: #0ca30c; }} .warning {{ fill: #fab219; }} .serious {{ fill: #ec835a; }} .critical {{ fill: #d03b3b; }}
  .wash-good {{ fill: #0ca30c; fill-opacity: .13; }} .wash-warning {{ fill: #fab219; fill-opacity: .16; }}
  .wash-serious {{ fill: #ec835a; fill-opacity: .16; }} .wash-critical {{ fill: #d03b3b; fill-opacity: .14; }}
  .wash-s1 {{ fill: #2a78d6; fill-opacity: .16; }} .ref {{ stroke: #2a78d6; stroke-width: 1; fill: none; }}
  .line {{ stroke: #2a78d6; stroke-width: 2; fill: none; stroke-linejoin: round; stroke-linecap: round; }}
  .dot {{ fill: #2a78d6; stroke: #fcfcfb; stroke-width: 2; }} .dot-raw {{ fill: #2a78d6; fill-opacity: .45; stroke: #fcfcfb; stroke-width: 1.5; }}
  .thin {{ stroke: #2a78d6; stroke-width: 1.25; stroke-opacity: .55; fill: none; stroke-linejoin: round; }}
  .mean {{ stroke: #2a78d6; stroke-width: 1.5; stroke-dasharray: 5 4; fill: none; }}
  .lim {{ stroke: #0ca30c; stroke-width: 1.25; stroke-dasharray: 4 3; fill: none; }} .lim-t {{ fill: #087a08; }} .pin {{ fill: #0b0b0b; stroke: #fcfcfb; stroke-width: 2; }}
  @media (prefers-color-scheme: dark) {{
    .bg {{ fill: #1a1a19; }} text {{ fill: #c3c2b7; }} .ink {{ fill: #f0efec; }}
    .grid {{ stroke: #2c2c2a; }} .axis {{ stroke: #383835; }} .track {{ fill: #262624; }}
    .s1 {{ fill: #3987e5; }} .s2 {{ fill: #d95926; }} .s3 {{ fill: #199e70; }} .s4 {{ fill: #c98500; }}
    .wash-s1 {{ fill: #3987e5; }} .ref, .line {{ stroke: #3987e5; }}
    .dot {{ fill: #3987e5; stroke: #1a1a19; }} .dot-raw {{ fill: #3987e5; stroke: #1a1a19; }}
    .thin, .mean {{ stroke: #3987e5; }} .lim {{ stroke: #2fbf2f; }} .lim-t {{ fill: #4fcf4f; }} .pin {{ fill: #f0efec; stroke: #1a1a19; }}
  }}
</style>"""

def svg(w, h, label, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h:g}" width="{w}" height="{h:g}" '
            f'role="img" aria-label="{escape(label)}">\n<title>{escape(label)}</title>\n{STYLE}\n'
            f'<rect class="bg" width="{w}" height="{h:g}" rx="8"/>\n' + "\n".join(body) + "\n</svg>\n")

def t(x, y, s, cls="", anchor="start", size=None, title=None):
    a = f' class="{cls}"' if cls else ""
    z = f' style="font-size:{size}px"' if size else ""      # 屬性會被 <style> 的 text{font-size} 蓋掉，要用 style
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}"{a}{z}>{escape(str(s))}</text>'

def bar_end(x0, y, w, h, cls, title=None):
    """左端方、右端 4px 圓角的長條。"""
    w = max(w, 2); r = min(4, h / 2, w)
    tt = f"<title>{escape(title)}</title>" if title else ""
    return (f'<path class="{cls}" d="M{x0:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z">{tt}</path>')

def status_mark(x, y, cls, label, anchor="end"):
    """狀態點＋文字（文字靠右對齊時點在文字左邊）。"""
    if anchor == "end":
        tw = 12.5 * len(label)
        return f'<circle class="{cls}" cx="{x - tw - 7:.1f}" cy="{y - 4:.1f}" r="4"/>' + t(x, y, label, anchor="end")
    return f'<circle class="{cls}" cx="{x + 4:.1f}" cy="{y - 4:.1f}" r="4"/>' + t(x + 12, y, label)

# 分類量尺（分界依機器螢幕刻度量得；女性的分界還沒有用女性的量測螢幕核對過）
PBF_CATS = {
    "男性": [(0, 5, "低脂肪", "s1"), (5, 12, "運動型", "s3"), (12, 18, "正常", "good"), (18, 25, "過重", "warning"), (25, 45, "肥胖", "critical")],
    "女性": [(0, 10, "低脂肪", "s1"), (10, 23, "運動型", "s3"), (23, 34, "正常", "good"), (34, 40, "過重", "warning"), (40, 45, "肥胖", "critical")],
}

def gauge_row(y, label, value, vtext, cats, ticks, pos, sub=None):
    """一列分類量尺：分類色帶＋名稱、從左端填到數值的長條、刻度、右側數值與分類。"""
    X0, X1 = 128, 430
    px = lambda v: X0 + (X1 - X0) * pos(v)
    cat = next((c for c in cats if c[0] <= value < c[1]), cats[-1] if value >= cats[-1][1] else cats[0])
    out = [t(0, y + 34, label, "ink")]
    if sub: out.append(t(0, y + 50, sub, "muted", size=11))
    for i, (a, b, name, cls) in enumerate(cats):
        x, x2 = px(a) + (1 if i else 0), px(b) - (1 if i < len(cats) - 1 else 0)
        out.append(f'<rect class="{cls}" x="{x:.1f}" y="{y + 6}" width="{max(x2 - x, 1):.1f}" height="5" rx="2"/>')
        out.append(t((px(a) + px(b)) / 2, y + 24, name, size=11, anchor="middle"))
    out.append(bar_end(X0, y + 30, px(min(max(value, cats[0][0]), cats[-1][1])) - X0, 8, cat[3], f"{label} {vtext}"))
    out.append(f'<line class="axis" x1="{X0}" x2="{X1}" y1="{y + 44}" y2="{y + 44}"/>')
    for tk in ticks:
        anc = "start" if tk == ticks[0] else "end" if tk == ticks[-1] else "middle"
        out.append(t(px(tk), y + 58, f"{tk:g}", "muted", anc, 11))
    out.append(t(560, y + 30, vtext, "ink b", "end", 15))
    out.append(status_mark(560, y + 50, cat[3], cat[2]))
    return out

def linear(lo, hi): return lambda v: (min(max(v, lo), hi) - lo) / (hi - lo)

def piecewise(edges):
    """各區段等寬（機器的內臟脂肪量尺 0–10–15–30 就是這樣畫的）。"""
    n = len(edges) - 1
    def f(v):
        v = min(max(v, edges[0]), edges[-1])
        for i in range(n):
            if v <= edges[i + 1]: return (i + (v - edges[i]) / (edges[i + 1] - edges[i])) / n
        return 1.0
    return f

def svg_assessment(m):
    v, sex = m["v"], m["person"]["sex"]
    lvl, sure = vfa_level_text(m["d"]["vfa_level"])
    rows = [
        ("體脂率", v["pbf_pct"], f"{fx(v['pbf_pct'], 2)}%", PBF_CATS[sex], list(range(0, 46, 5)), linear(0, 45), None),
        ("內臟脂肪等級", m["d"]["vfa_level"], lvl + ("" if sure else "（?）"),
         [(0, 10, "風險低", "s1"), (10, 15, "中等", "good"), (15, 30, "肥胖", "critical")], [0, 10, 15, 30],
         piecewise([0, 10, 15, 30]), f"= 面積 {fx(v['vfa_cm2'], 2)} ÷ 10"),
        ("體重（標準%）", v["weight_pct_of_std"], f"{fx(v['weight_pct_of_std'], 1)}%",
         [(55, 85, "風險低", "s1"), (85, 115, "中等", "good"), (115, 160, "肥胖", "critical")],
         [55, 70, 85, 100, 115, 130, 145, 160], linear(55, 160), None),
        ("BMI", v["bmi"], fx(v["bmi"], 2),
         [(15, 20, "過輕", "warning"), (20, 25, "正常", "good"), (25, 30, "過重", "warning"), (30, 45, "肥胖", "critical")],
         [15, 20, 25, 30, 35, 40, 45], linear(15, 45), "分區照機器圖"),
    ]
    body = []
    for i, r in enumerate(rows):
        body += gauge_row(8 + i * 72, *r[:6], sub=r[6])
    return svg(560, 8 + len(rows) * 72, "體組成評估：體脂率、內臟脂肪等級、體重標準百分比、BMI", body)

def svg_bone(m):
    W, H, L, R, T, B = 560, 240, 34, 12, 12, 30
    x = lambda a: L + (W - L - R) * (a - 20) / 80
    y = lambda s: T + (H - T - B) * (2 - s) / 7
    ctr = lambda a: 0 if a <= 55 else -1.6 * (a - 55) / 45      # 同齡參考帶：依螢幕描繪
    body = []
    for a, b, cls in ((2, -2, "good"), (-2, -3, "warning"), (-3, -4, "serious"), (-4, -5, "critical")):
        body.append(f'<rect class="wash-{cls}" x="{L}" y="{y(a):.1f}" width="{W - L - R}" height="{y(b) - y(a):.1f}"/>')
    ages = [20, 55, 100]
    band = [f"{x(a):.1f},{y(ctr(a) + 1):.1f}" for a in ages] + [f"{x(a):.1f},{y(ctr(a) - 1):.1f}" for a in reversed(ages)]
    body.append(f'<polygon class="wash-s1" points="{" ".join(band)}"/>')
    body.append(f'<polyline class="ref" points="{" ".join(f"{x(a):.1f},{y(ctr(a)):.1f}" for a in ages)}"/>')
    for s in range(2, -6, -1):
        body.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(s):.1f}" y2="{y(s):.1f}" stroke-opacity=".7"/>')
        body.append(t(L - 6, y(s) + 4, f"{s:+d}".replace("+0", "0").replace("-", "−"), "muted", "end", 11))
    for a in range(20, 101, 10):
        body.append(t(x(a), H - 12, a, "muted", "middle", 11))
    ts, age = m["v"]["bmd_tscore"], m["real_age"]
    body.append(f'<circle class="pin" cx="{x(age):.1f}" cy="{y(ts):.1f}" r="5"><title>{age} 歲，T-score {sg(ts)}</title></circle>')
    body.append(t(x(age) + 10, y(ts) + 4, f"{fx(m['v']['bmd_gcm2'], 2)} g/cm² · T {sg(ts)}", "ink b"))
    return svg(W, H, f"全身骨密度 {fx(m['v']['bmd_gcm2'], 2)} g/cm²，YA T-score {sg(ts)}，{age} 歲", body)

def score_rows(items, lo, hi, title):
    """評分列：從左端填到分數（跟機器一樣），右側數值與分數。items: (名稱, 數值文字, 分數|None, 可信度|None, 備註)"""
    X0, X1, RH = 150, 420, 46
    px = lambda s: X0 + (X1 - X0) * (min(max(s, lo), hi) - lo) / (hi - lo)
    body = []
    for i, (name, vtext, score, conf, why) in enumerate(items):
        y = 6 + i * RH
        body.append(t(0, y + 20, name, "ink"))
        body.append(f'<rect class="track" x="{X0}" y="{y + 10}" width="{X1 - X0}" height="8" rx="4"/>')
        for k in range(lo, hi + 1):
            body.append(f'<line class="axis" x1="{px(k):.1f}" x2="{px(k):.1f}" y1="{y + 21}" y2="{y + 24}"/>')
            lab = f"{k:+d}".replace("+0", "0").replace("-", "−")
            body.append(t(px(k), y + 36, lab, "muted", "start" if k == lo else "end" if k == hi else "middle", 10))
        body.append(t(560, y + 18, vtext, "ink b", "end", 14))
        if score is None:
            body.append(t((X0 + X1) / 2, y + 7, "QR 裡沒有這個評分", "muted", "middle", 10))
            body.append(t(560, y + 36, "評分：—", "muted", "end", 11))
            continue
        cls, lab = status(score)
        tip = f"{name} 評分 {sg(score)}（{conf}：{why}）"
        body.append(bar_end(X0, y + 10, px(score) - X0, 8, cls, tip))
        mark = "" if conf == "確認" else "≈"
        body.append(t(560, y + 36, f"評分 {mark}{sg(score)} · {lab}", "", "end", 11))
    return svg(560, 6 + len(items) * RH + 4, title, body)

def svg_risk(m):
    v, s, seg = m["v"], m["scores"], m["segments"]
    it = lambda k: (s[k][0], s[k][1], s[k][2])
    return score_rows([
        ("骨礦物量", f"{fx(v['bmc_kg'], 2)} kg", *it("bmc")),
        ("上肢肌肉量", f"{fx(v['upper_limb_muscle_kg'], 2)} kg", *it("upper_limb_muscle")),
        ("下肢肌肉量", f"{fx(v['lower_limb_muscle_kg'], 2)} kg", *it("lower_limb_muscle")),
        ("軀幹肌肉量", f"{fx(seg['軀幹']['muscle_kg'], 2)} kg", *it("trunk_muscle")),
        ("骨骼肌質量指數", f"{fx(v['smi'], 2)} kg/m²", *it("smi")),
        ("四肢肌肉質量指數", f"{fx(v['asmi'], 2)} kg/m²", *it("asmi")),
    ], -4, 4, "風險指標（−4 風險高 … +4 風險低）")

def svg_scores(m):
    v, s = m["v"], m["scores"]
    it = lambda k: (s[k][0], s[k][1], s[k][2])
    return score_rows([
        ("體脂肪量", f"{fx(v['fat_kg'])} kg", *it("fat")),
        ("肌肉質量", f"{fx(v['muscle_kg'])} kg", *it("muscle")),
        ("骨礦物", f"{fx(v['bmc_kg'])} kg", *it("bmc")),
        ("總體水分", f"{fx(v['tbw_kg'])} L", *it("tbw")),
        ("骨骼肌質量", f"{fx(v['smm_kg'])} kg", None, None, None),
        ("相位角", f"{fx(v['phase_angle'])}°", *it("pa")),
    ], -3, 3, "主要指標評分（−3 風險高 … +3 優）")

def svg_composition(m):
    d = m["d"]
    parts = [("肌肉", d["pct_muscle"], "s1"), ("非基礎脂肪", d["pct_storage_fat"], "s2"),
             ("骨礦物", d["pct_bmc"], "s3"), ("基礎脂肪", d["pct_essential_fat"], "s4")]
    W, total = 560, sum(p for _, p, _ in parts)
    body, x = [], 0.0
    for i, (name, p, cls) in enumerate(parts):
        w = (W - 2 * (len(parts) - 1)) * p / total
        rx = ' rx="4"' if i in (0, len(parts) - 1) else ""
        body.append(f'<rect class="{cls}" x="{x:.1f}" y="6" width="{w:.1f}" height="14"{rx}><title>{name} {fx(p)}%</title></rect>')
        x += w + 2
    lx = 0
    for name, p, cls in parts:
        label = f"{name} {fx(p)}%"
        body.append(f'<rect class="{cls}" x="{lx}" y="34" width="10" height="10" rx="2"/>')
        body.append(t(lx + 16, 43, label))
        lx += 16 + 13 * len(label) + 14
    return svg(W, 54, "身體組成比例：" + "、".join(f"{n} {fx(p)}%" for n, p, _ in parts), body)

SEG_ORDER = ["右上肢", "左上肢", "軀幹", "右下肢", "左下肢"]
def svg_segments(m):
    LW, CW, RH, TOP = 64, 168, 34, 30
    W = LW + CW * 3
    body = []
    for c, (key, name) in enumerate((("muscle", "肌肉量"), ("fat", "體脂肪量"), ("bone", "骨礦物量"))):
        x0 = LW + c * CW
        body.append(t(x0, 18, name, "ink b"))
        bx0, bx1 = x0 + 66, x0 + 124
        sx = lambda s: bx0 + (bx1 - bx0) * (min(max(s, -3), 3) + 3) / 6
        body.append(f'<line class="axis" x1="{sx(0):.1f}" x2="{sx(0):.1f}" y1="{TOP - 4}" y2="{TOP + RH * 5 - 6}"/>')
        for i, seg in enumerate(SEG_ORDER):
            g = m["segments"][seg]; kg, sc = g[f"{key}_kg"], g[f"{key}_score"]
            yc = TOP + i * RH + RH / 2 - 4
            cls, lab = status(sc)
            body.append(t(x0, yc + 4, f"{fx(kg, 2)} kg", "ink", size=12))
            body.append(f'<rect class="track" x="{bx0}" y="{yc - 3}" width="{bx1 - bx0}" height="6" rx="3"/>')
            a, b = sorted((sx(0), sx(sc)))
            body.append(f'<rect class="{cls}" x="{a:.1f}" y="{yc - 5}" width="{max(b - a, 2):.1f}" height="10" rx="3">'
                        f'<title>{seg} {name} {fx(kg, 2)} kg，評分 {sg(sc)}（{lab}）</title></rect>')
            body.append(t(x0 + 160, yc + 4, sg(sc), "", "end", 11))
    for i, seg in enumerate(SEG_ORDER):
        body.append(t(0, TOP + i * RH + RH / 2, seg, "ink"))
    body.append(t(0, TOP + 5 * RH + 10, "評分 −3 風險高 … +3 優；長條從 0 往分數方向畫", "muted", size=11))
    return svg(W, TOP + 5 * RH + 18, "各肢段 肌肉／體脂肪／骨礦物 kg 與評分", body)

# ---------------- 趨勢 ----------------
# 每個指標用合適的畫法（畫法欄）：
#   trend   會慢慢變、值得看趨勢：點＋趨勢線＋「趨勢 ± 波動」帶；標近 4 週斜率 ± 標準誤、方向是否明確
#   derived 由別的指標直接算出（說明欄 = 來源指標|公式）：點依序連線，不另算趨勢（形狀和來源指標一樣）
#   stable  幾週內不該真的改變（或只到小數一位）：點＋平均線＋平均 ± 標準差帶，不畫趨勢線（說明欄 = 原因）
TRENDS = [  # (key, 名稱, 單位, 小數, 方向 +1 越高越好 / −1 越低越好 / 0 不判斷, 畫法, 說明)
    ("weight_kg", "體重", "kg", 1, -1, "trend", ""),
    ("pbf_pct", "體脂率", "%", 2, -1, "trend", ""),
    ("fat_kg", "脂肪質量", "kg", 2, -1, "trend", ""),
    ("vfa_cm2", "內臟脂肪面積", "cm²", 2, -1, "trend", ""),
    ("smm_kg", "骨骼肌質量", "kg", 2, 1, "trend", ""),
    ("muscle_kg", "肌肉質量", "kg", 2, 1, "trend", ""),
    ("ffm_kg", "去脂質量", "kg", 2, 1, "trend", ""),
    ("bmd_gcm2", "骨密度", "g/cm²", 2, 1, "stable", "骨密度幾週內不會真的改變，起伏是量測誤差"),
    ("tbw_kg", "總體水分", "kg", 2, 0, "trend", ""),
    ("ecw_ratio", "細胞外液比 ECW/TBW", "", 3, 0, "stable", "看水分分布有沒有異常，短期起伏是當天狀態與誤差"),
    ("phase_angle", "相位角", "°", 1, 1, "stable", "只到小數一位、短期變化小，起伏多半是量測誤差"),
    ("bmr_kcal", "基礎代謝率", "kcal", 0, 0, "derived", "ffm_kg|= 370 + 21.6 × 去脂質量"),
    ("protein_kg", "蛋白質", "kg", 2, 1, "trend", ""),
    ("bmi", "BMI", "", 2, -1, "derived", "weight_kg|= 體重 ÷ 身高²"),
    ("smi", "骨骼肌質量指數", "kg/m²", 2, 1, "derived", "smm_kg|= 骨骼肌質量 ÷ 身高²"),
]
TREND = {k: (n, u, d, dr) for k, n, u, d, dr, *_ in TRENDS}
KIND = {k: (kind, note) for k, *_, kind, note in TRENDS}

# 「對照正常範圍」圖：None = 用機器每次量測給的範圍（ranges.csv）；數組 = 固定範圍
RANGE_CHART = {"weight_kg": None, "pbf_pct": None, "fat_kg": None, "vfa_cm2": None, "ffm_kg": None,
               "tbw_kg": None, "protein_kg": None, "bmi": (20.0, 25.0)}
RANGE_SRC = {"vfa_cm2": "一般參考值（< 100 cm²）", "bmi": "機器 BMI 圖的「正常」區"}

# 歷史報表的分組：(標題, 說明, 指標)
TREND_GROUPS = [
    ("會慢慢變的：看趨勢",
     "點 = 每次量測；線 = 趨勢（7 天尺度的加權局部迴歸：每個時間點用前後的量測擬合一條直線，越近權重越高）；淡藍帶 = 趨勢 ± 波動。"
     "**近 4 週 a ± b /週**：近 4 週所有量測擬合一條直線的斜率（每週變化）± 標準誤 —— 用 4 週的全部量測算，"
     "不會被最後一兩次的起伏帶走；斜率超過 2 倍標準誤才標「明確上升／下降」，否則「還看不出方向」。"
     "**波動**：每次量測離趨勢線的標準差，單次量測大概就在趨勢上下這麼多之內跳，比它小的單日變化不用在意"
     "（體脂率的波動約 ±0.6%，和文獻上 BIA 隔天重測約 1% 的誤差一致）。"
     "右邊「對照正常範圍」的圖把正常範圍（綠帶，虛線 = 上下限）一起畫進來，看離目標還有多遠。",
     ["weight_kg", "pbf_pct", "fat_kg", "vfa_cm2", "ffm_kg", "tbw_kg", "protein_kg", "smm_kg", "muscle_kg"]),
    ("由其他指標算出來的：不另算趨勢",
     "BMI = 體重 ÷ 身高²、基礎代謝率 = 370 + 21.6 × 去脂質量、骨骼肌質量指數 = 骨骼肌 ÷ 身高²（QR 欄位關係檢查每次都驗證這三條）。"
     "和來源指標是同一組數字，形狀一模一樣，所以只把點依序連起來、不另算趨勢 —— 趨勢請看體重、去脂質量、骨骼肌質量那幾張。",
     ["bmi", "bmr_kcal", "smi"]),
    ("短期不該變的：看平均與波動",
     "骨密度幾週到幾個月內不會真的改變；相位角只到小數一位、短期變化小；細胞外液比看水分分布有沒有異常。"
     "這幾個的起伏多半是量測誤差，畫趨勢線會誤導，所以只畫平均（虛線）± 1 標準差（淡藍帶）。"
     "要累積幾個月、平均明顯移動了才有意義。",
     ["bmd_gcm2", "phase_angle", "ecw_ratio"]),
]

def nice(lo, hi, n=3):
    import math
    raw = (hi - lo) / n or 1
    mag = 10 ** math.floor(math.log10(raw))
    step = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    out, v = [], math.floor(lo / step) * step
    while True:                                   # 最後一個刻度要 ≥ hi，最高點才不會畫到圖外
        out.append(round(v, 6))
        if v >= hi - step * 1e-6: return out
        v += step

def delta_text(key, a, b):
    n, u, d, dr = TREND[key]
    dv = b - a
    if fx(abs(dv), d) == fx(0, d): return f"±0 {u}".strip(), ""
    word = "" if dr == 0 or KIND[key][0] == "stable" else ("改善" if (dv > 0) == (dr > 0) else "變差")
    return f"{sg(dv, d)} {u}".strip(), word

TREND_BW_DAYS = 7.0      # 趨勢線的時間尺度（高斯核 σ，天）
SLOPE_WINDOW_DAYS = 28   # 斜率用最近幾天的量測

def local_linear(xs, ys, at, bw=TREND_BW_DAYS):
    """高斯核加權的局部線性迴歸（LOESS 的簡化版）：在 at 這個時間點，用前後的量測擬合一條直線，
    離 at 越遠權重越低（σ = bw 天）。回傳 (趨勢值, 斜率／天)。只用來畫線：量測間隔不規則（1–6 天），
    移動平均會落後，局部線性在最新一點也不會落後。它在端點的斜率只靠最後幾點、很不穩，所以圖上標的
    斜率改用 trend_stats() 的近 4 週直線擬合。"""
    import math
    w = [math.exp(-0.5 * ((x - at) / bw) ** 2) for x in xs]
    sw = sum(w)
    mx = sum(wi * x for wi, x in zip(w, xs)) / sw
    my = sum(wi * y for wi, y in zip(w, ys)) / sw
    sxx = sum(wi * (x - mx) ** 2 for wi, x in zip(w, xs))
    b = sum(wi * (x - mx) * (y - my) for wi, x, y in zip(w, xs, ys)) / sxx if sxx > 1e-9 else 0.0
    return my + b * (at - mx), b

def trend_stats(xs, ys):
    """趨勢的兩個數字：
    斜率 ± 標準誤：最近 SLOPE_WINDOW_DAYS 天的量測做最小平方直線（不到 4 點就用最後 4 點），/週；
                   |斜率| ≥ 2 × 標準誤 → 方向明確（約 95% 信心不是 0）。
    波動：每次量測離趨勢線（local_linear）的標準差。點太少（< 4 點或 < 3 天）回傳 None。"""
    import math
    if len(xs) < 4 or xs[-1] - xs[0] < 3: return None
    idx = [i for i, x in enumerate(xs) if x >= xs[-1] - SLOPE_WINDOW_DAYS]
    if len(idx) < 4: idx = list(range(len(xs) - 4, len(xs)))
    wx, wy = [xs[i] for i in idx], [ys[i] for i in idx]
    n = len(wx)
    mx, my = sum(wx) / n, sum(wy) / n
    sxx = sum((x - mx) ** 2 for x in wx)
    if sxx < 1e-6: return None
    b = sum((x - mx) * (y - my) for x, y in zip(wx, wy)) / sxx
    se = math.sqrt(sum((y - my - b * (x - mx)) ** 2 for x, y in zip(wx, wy)) / (n - 2) / sxx)
    sd = math.sqrt(sum((y - local_linear(xs, ys, x)[0]) ** 2 for x, y in zip(xs, ys)) / (len(xs) - 1))
    span = xs[-1] - wx[0]
    return {"slope": b * 7, "se": se * 7, "sd": sd, "n": n, "clear": b != 0 and abs(b) >= 2 * se,
            "label": "近 4 週" if 21 <= span <= SLOPE_WINDOW_DAYS + 0.01 else f"近 {round(span)} 天"}

GEOM = (340, 216, 44, 14, 84, 42)   # W, H, 左右上下邊界

def series(ms, key):
    from datetime import datetime
    pts = [(datetime.strptime(m["measured_at"], "%Y-%m-%d %H:%M"), m["v"][key], m) for m in ms]
    xs = [(p[0] - pts[0][0]).total_seconds() / 86400 for p in pts]       # 第一次量測起算的天數
    return pts, xs, [p[1] for p in pts]

def y_ticks(vals, d, pad, n=3):
    a, b = min(vals), max(vals)
    sp = max((b - a) * pad, abs(a + b) / 2 * 0.02, 10 ** -d)
    lo = a - sp
    if a >= 0: lo = max(lo, 0)
    return nice(lo, b + sp, n)

def frame(pts, xs, ticks):
    """座標轉換＋水平格線、y 軸數字、x 軸日期刻度（短期每天／兩天，否則每週一）。"""
    from datetime import timedelta
    W, H, L, R, T, B = GEOM
    lo, hi = ticks[0], ticks[-1]
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    span = xs[-1] - xs[0]
    pad = span * 0.06 or 1
    x = (lambda dd: (L + W - R) / 2) if len(pts) == 1 else (lambda dd: L + (W - L - R) * (dd - xs[0] + pad) / (span + 2 * pad))
    body = []
    for tk in ticks:
        body.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(tk):.1f}" y2="{y(tk):.1f}"/>')
        body.append(t(L - 6, y(tk) + 4, f"{tk:g}", "muted", "end", 11))
    if len(pts) > 1:
        day = lambda dt: (dt - pts[0][0]).total_seconds() / 86400
        first = pts[0][0].replace(hour=0, minute=0)
        stepd = 1 if span <= 6 else 2 if span <= 13 else 7 if span <= 70 else 14 if span <= 140 else 30
        tick = first if stepd < 7 else first + timedelta(days=(7 - first.weekday()) % 7)
        while day(tick) <= xs[-1] + pad:
            if day(tick) >= xs[0] - pad:
                body.append(f'<line class="axis" x1="{x(day(tick)):.1f}" x2="{x(day(tick)):.1f}" y1="{H - B}" y2="{H - B + 4}"/>')
                body.append(t(x(day(tick)), H - B + 16, tick.strftime("%m-%d"), "muted", "middle", 10))
            tick += timedelta(days=stepd)
    return x, y, body

def loess_grid(xs, ys, n=80):
    if len(xs) < 3 or xs[-1] - xs[0] < 3: return []
    return [(g,) + local_linear(xs, ys, g) for g in (xs[0] + (xs[-1] - xs[0]) * i / n for i in range(n + 1))]

def polyline(cls, pairs, x, y):
    return f'<polyline class="{cls}" points="{" ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in pairs)}"/>'

def band(cls, xs_, lows, highs, x, y):
    pts_ = [f"{x(a):.1f},{y(b):.1f}" for a, b in zip(xs_, highs)] + [f"{x(a):.1f},{y(b):.1f}" for a, b in reversed(list(zip(xs_, lows)))]
    return f'<polygon class="{cls}" points="{" ".join(pts_)}"/>'

def dots(xs, ys, tips, x, y, cls="dot-raw"):
    return [f'<g><title>{escape(tp)}</title><circle cx="{x(a):.1f}" cy="{y(b):.1f}" r="11" fill="transparent"/>'
            f'<circle class="{cls}" cx="{x(a):.1f}" cy="{y(b):.1f}" r="4"/></g>' for a, b, tp in zip(xs, ys, tips)]

def extremes(xs, ys, d, x, y):
    """最高、最低點標數值（最新值上面已經有大字）。"""
    W, H, L, R, T, B = GEOM
    out = []
    if len(ys) < 3: return out
    for i in sorted({ys.index(max(ys)), ys.index(min(ys))} - {len(ys) - 1}):
        px, py, above = x(xs[i]), y(ys[i]), ys[i] == max(ys)
        ly = py - 9 if above else py + 17
        if T + 2 <= ly - 8 and ly <= H - B - 4:             # 上下有空間：放點的正上／正下方
            out.append(t(px, ly, fx(ys[i], d), "", "middle", 10))
        else:                                               # 太靠邊：放點的旁邊
            right = px < (L + W - R) / 2
            out.append(t(px + (8 if right else -8), py + 4, fx(ys[i], d), "", "start" if right else "end", 10))
    return out

def text_w(s, size=10):
    return sum(size if ord(c) > 0x2E80 else size * 0.58 for c in s)

def legend(items):
    """圖例一列（底部）：(符號, 文字)，符號 dot／line／thin／mean／band／range。"""
    W, H, L, R, T, B = GEOM
    out, xx, yc = [], L, H - 9
    for kind, text in items:
        if kind == "dot": out.append(f'<circle class="dot-raw" cx="{xx + 4}" cy="{yc}" r="4"/>'); xx += 12
        elif kind in ("line", "thin", "mean"):
            out.append(f'<line class="{kind}" x1="{xx}" x2="{xx + 16}" y1="{yc}" y2="{yc}"/>'); xx += 20
        else: out.append(f'<rect class="{"wash-s1" if kind == "band" else "wash-good"}" x="{xx}" y="{yc - 5}" width="14" height="10" rx="2"/>'); xx += 18
        out.append(t(xx, H - 5, text, "muted", size=10)); xx += text_w(text) + 12
    return out

def svg_trend(ms, key):
    """一般尺度（y 軸依資料）的圖，依畫法分三種，見 TRENDS 上方說明。"""
    name, unit, d, dr = TREND[key]
    kind, note = KIND[key]
    u = f" {unit}" if unit else ""
    pts, xs, ys = series(ms, key)
    W, H, L, R, T, B = GEOM
    sd_ = max(d, 2)
    body = [t(14, 22, name, "ink b", size=13), t(14, 50, fx(ys[-1], d), "ink b", size=22),
            t(14 + 13.5 * len(fx(ys[-1], d)) + 4, 50, unit)]
    tips = [f"{m['measured_at']}：{fx(v, d)}{u}" for _, v, m in pts]
    label = f"{name}：" + "、".join(f"{m['measured_at']} {fx(v, d)}{unit}" for _, v, m in pts)
    if kind != "stable" and len(pts) > 1:
        dt, word = delta_text(key, ys[-2], ys[-1])
        body.append(t(W - R, 22, f"{dt} 較上次" + (f"（{word}）" if word else ""), "", "end", 11))
    vals, layers, items = list(ys), [], [("dot", "每次量測")]
    if kind == "trend":
        grid, st = loess_grid(xs, ys), trend_stats(xs, ys)
        if grid:
            s = st["sd"] if st else 0
            gx = [g[0] for g in grid]
            vals += [g[1] - s for g in grid] + [g[1] + s for g in grid]
            layers.append(lambda x, y: band("wash-s1", gx, [g[1] - s for g in grid], [g[1] + s for g in grid], x, y))
            layers.append(lambda x, y: polyline("line", [(g[0], g[1]) for g in grid], x, y))
            tips = [tp + f"（趨勢 {fx(local_linear(xs, ys, xx)[0], d)}）" for tp, xx in zip(tips, xs)]
            items += [("line", "趨勢"), ("band", "趨勢 ± 波動")] if st else [("line", "趨勢")]
        elif len(pts) > 1:
            layers.append(lambda x, y: polyline("thin", zip(xs, ys), x, y))
        if st:
            body.append(t(W - R, 50, f"{st['label']} {sg(st['slope'], sd_)} ± {fx(st['se'], sd_)}{u}/週", "", "end", 11))
            body.append(t(14, 68, f"波動 ±{fx(st['sd'], sd_)}{u}", "muted", size=11))
            if st["clear"]:
                up = st["slope"] > 0
                verdict = ("明確上升" if up else "明確下降") + ("" if not dr else "（改善）" if up == (dr > 0) else "（變差）")
            else:
                verdict = "還看不出方向（斜率 < 2 倍誤差）"
            body.append(t(W - R, 68, verdict, "ink b" if st["clear"] else "muted", "end", 11))
            label += f"；{st['label']}斜率 {sg(st['slope'], sd_)} ± {fx(st['se'], sd_)}{unit}/週，{verdict}；波動 ±{fx(st['sd'], sd_)}{unit}"
    elif kind == "derived":
        src, formula = note.split("|")
        if len(pts) > 1:
            layers.append(lambda x, y: polyline("thin", zip(xs, ys), x, y))
            items.append(("thin", "依序連線（不是趨勢）"))
        body.append(t(W - R, 50, formula, "", "end", 11))
        body.append(t(14, 68, f"由{TREND[src][0]}算出，趨勢看「{TREND[src][0]}」那張", "muted", size=11))
        label += f"；{formula}，趨勢看{TREND[src][0]}"
    else:  # stable
        import math
        n = len(ys)
        mean = sum(ys) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in ys) / (n - 1)) if n > 1 else 0.0
        vals += [mean - sd, mean + sd]
        layers.append(lambda x, y: f'<rect class="wash-s1" x="{L}" y="{y(mean + sd):.1f}" width="{W - L - R}" height="{y(mean - sd) - y(mean + sd):.1f}"/>')
        layers.append(lambda x, y: f'<line class="mean" x1="{L}" x2="{W - R}" y1="{y(mean):.1f}" y2="{y(mean):.1f}"/>')
        items += [("mean", "平均"), ("band", "平均 ± 1 標準差")]
        body.append(t(W - R, 22, f"{n} 次：{fx(min(ys), d)}–{fx(max(ys), d)}{u}", "", "end", 11))
        body.append(t(W - R, 50, f"平均 {fx(mean, d + 1)} ± {fx(sd, d + 1)}{u}", "", "end", 11))
        body.append(t(14, 68, note, "muted", size=11))
        label += f"；平均 {fx(mean, d + 1)} ± {fx(sd, d + 1)}{unit}（{note}）"
    x, y, axes = frame(pts, xs, y_ticks(vals, d, 0.25))
    body += axes + [f(x, y) for f in layers]
    body += dots(xs, ys, tips, x, y) + extremes(xs, ys, d, x, y)
    if len(items) > 1: body += legend(items)
    return svg(W, H, label, body)

def range_of(m, key):
    return RANGE_CHART[key] or m["ranges"].get(key)

def svg_range(ms, key):
    """對照正常範圍的圖：y 軸同時涵蓋正常範圍與資料，綠帶 = 範圍、虛線 = 上下限，標出離範圍多遠。"""
    name, unit, d, dr = TREND[key]
    kind = KIND[key][0]
    u = f" {unit}" if unit else ""
    pts, xs, ys = series(ms, key)
    W, H, L, R, T, B = GEOM
    rs = [(xx, range_of(m, key)) for xx, (_, _, m) in zip(xs, pts) if range_of(m, key)]
    lo_r, hi_r = rs[-1][1]
    v = ys[-1]
    src = RANGE_SRC.get(key, "機器每次量測給的正常範圍")
    whole = lo_r == round(lo_r) and hi_r == round(hi_r)
    fb = lambda b: f"{b:g}" if whole else fx(b, d)               # 兩個界線都是整數（5–20、< 100）就不補小數
    rtxt = f"< {fb(hi_r)}" if lo_r == 0 else f"{fb(lo_r)}–{fb(hi_r)}"
    body = [t(14, 22, f"{name} · 對照正常範圍", "ink b", size=13), t(W - R, 22, f"正常 {rtxt}{u}", "", "end", 11)]
    if v > hi_r or v < lo_r:
        big, word = sg(v - (hi_r if v > hi_r else lo_r), d), "高於上限" if v > hi_r else "低於下限"
        body += [t(14, 50, big, "ink b", size=22), t(14 + 13.5 * len(big) + 4, 50, unit),
                 t(W - R, 50, f"{word} {fb(hi_r if v > hi_r else lo_r)}{u}", "ink b", "end", 11)]
    else:
        word = "在範圍內"
        body += [t(14, 50, word, "ink b", size=22),
                 t(W - R, 50, f"距上限 {fx(hi_r - v, d)} · 距下限 {fx(v - lo_r, d)}{u}", "", "end", 11)]
    body.append(t(14, 68, f"最新 {fx(v, d)}{u} · 綠帶 = {src}", "muted", size=11))
    def where(val, r):
        return f"高於上限 {sg(val - r[1], d)}" if val > r[1] else f"低於下限 {sg(val - r[0], d)}" if val < r[0] else "在範圍內"
    tips = [f"{m['measured_at']}：{fx(val, d)}{u}（{where(val, range_of(m, key))}）" if range_of(m, key) else
            f"{m['measured_at']}：{fx(val, d)}{u}" for _, val, m in pts]
    grid = loess_grid(xs, ys) if kind == "trend" else []
    vals = ys + [r[0] for _, r in rs] + [r[1] for _, r in rs] + [g[1] for g in grid]
    x, y, axes = frame(pts, xs, y_ticks(vals, d, 0.06, 4))
    # 範圍帶：每次量測的範圍略有不同，左右延伸到圖的邊緣
    bx = [xs[0] - 1e6] + [a for a, _ in rs] + [xs[-1] + 1e6] if len(pts) > 1 else [-1e6, 1e6]
    blo = [rs[0][1][0]] + [r[0] for _, r in rs] + [rs[-1][1][0]] if len(pts) > 1 else [lo_r] * 2
    bhi = [rs[0][1][1]] + [r[1] for _, r in rs] + [rs[-1][1][1]] if len(pts) > 1 else [hi_r] * 2
    cx = lambda dd: min(max(x(dd), L), W - R)
    body.append(band("wash-good", bx, blo, bhi, cx, y))
    body += axes
    body.append(polyline("lim", zip(bx, bhi), cx, y))
    body.append(t(W - R - 2, y(hi_r) - 5, f"上限 {fb(hi_r)}", "lim-t", "end", 10))
    if lo_r > 0:                                   # 下限是 0（內臟脂肪 < 100）就不畫下限線
        body.append(polyline("lim", zip(bx, blo), cx, y))
        body.append(t(W - R - 2, y(lo_r) - 5, f"下限 {fb(lo_r)}", "lim-t", "end", 10))
    if grid: body.append(polyline("line", [(g[0], g[1]) for g in grid], x, y))
    elif len(pts) > 1: body.append(polyline("thin", zip(xs, ys), x, y))
    body += dots(xs, ys, tips, x, y)
    body += legend([("dot", "每次量測")] + ([("line", "趨勢")] if grid else [("thin", "依序連線")] if len(pts) > 1 else []) +
                   [("range", "正常範圍")])
    label = f"{name}對照正常範圍（{src} {rtxt}{unit}）：最新 {fx(v, d)}{unit}，{word}" + \
            (f" {fx(abs(v - (hi_r if v > hi_r else lo_r)), d)}{unit}" if word != "在範圍內" else "")
    return svg(W, H, label, body)

# ---------------- 文件區塊（同一份內容 → Markdown 與 HTML） ----------------
CONF_NOTE = [
    ("✅ 確認", "所有表格數值與範圍、各肢段 kg 與評分、9 個整體評分（脂肪、肌肉、骨礦物、水分、上肢、下肢、軀幹、SMI、ASMI）、"
              "骨密度 T-score — 本工具的作者用三天的螢幕照片逐項對照，三天都吻合"
              "（[驗證方法與結果](../../docs/VALIDATION.md)）。這份報告用的是同一組欄位位置；"
              "如果你的機器韌體不同，可以用自己的螢幕照片重新驗證。"),
    ("🟡 推定", "相位角評分（標「≈」）：QR 用一位小數的相位角計算，螢幕用未進位的值，差最多 ±0.07；"
              "內臟脂肪等級（= 面積 ÷ 10；取整規則未知，小數 ≥ .5 時標「?」）；體脂率與 BMI 的分類分界（依螢幕刻度量得，女性的分界未經核對）；"
              "骨密度圖的同齡參考帶（依螢幕描繪）。"),
    ("⚪ 未還原", "骨骼肌評分：螢幕上有長條，但 QR 裡沒有吻合的欄位（可能是機器顯示時才計算）；"
              "BMI 各年齡風險曲線；相位角百分位圖；生物阻抗向量圖。"),
    ("ℹ️ 本 repo 自訂", "評分的狀態文字與顏色（良好／尚可／注意／警示／風險高，以 0／−1／−2／−2.5 分界）；「與上次比較」的改善／變差判斷。機器只用漸層色。"),
]

COMP_ROWS = [  # (名稱, key, 單位, 範圍 key, 小數)
    ("體重", "weight_kg", "kg", "weight_kg", 1), ("脂肪質量", "fat_kg", "kg", "fat_kg", 1),
    ("　必須脂肪", "essential_fat_kg", "kg", None, 1), ("　儲存脂肪", "storage_fat_kg", "kg", None, 1),
    ("體脂率", "pbf_pct", "%", "pbf_pct", 1), ("去脂質量", "ffm_kg", "kg", "ffm_kg", 1),
    ("　去脂質量（% 體重）", "ffm_pct", "%", None, 1), ("蛋白質質量", "protein_kg", "kg", "protein_kg", 1),
    ("總體水分", "tbw_kg", "kg", "tbw_kg", 1), ("　細胞內液", "icw_kg", "kg", None, 1), ("　細胞外液", "ecw_kg", "kg", None, 1),
    ("骨礦物質", "bmc_kg", "kg", "bmc_kg", 1), ("身體細胞質量", "body_cell_mass_kg", "kg", "body_cell_mass_kg", 1),
    ("細胞外質量", "ecm_kg", "kg", "ecm_kg", 1),
]
OTHER_ROWS = [
    ("BMI", "bmi", "", 2, "螢幕上只有圖；= 體重 ÷ 身高² 吻合"), ("內臟脂肪面積", "vfa_cm2", "cm²", 2, "一般參考值 < 100"),
    ("骨骼肌質量", "smm_kg", "kg", 1, ""), ("肌肉質量", "muscle_kg", "kg", 2, ""),
    ("骨密度", "bmd_gcm2", "g/cm²", 2, ""), ("骨密度 YA T-score", "bmd_tscore", "", 2, ""),
    ("相位角", "phase_angle", "°", 1, ""), ("骨骼肌質量指數 SMI", "smi", "kg/m²", 2, ""),
    ("四肢肌肉質量指數 ASMI", "asmi", "kg/m²", 2, ""), ("上肢肌肉量", "upper_limb_muscle_kg", "kg", 2, ""),
    ("下肢肌肉量", "lower_limb_muscle_kg", "kg", 2, ""), ("體重（標準%）", "weight_pct_of_std", "%", 1, ""),
    ("基礎代謝率", "bmr_kcal", "kcal", 0, "螢幕上沒有；= 370 + 21.6 × 去脂質量 吻合"),
    ("細胞外液比 ECW/TBW", "ecw_ratio", "", 3, "自行計算（螢幕上沒有）"),
]

def judge(v, rng):
    if not rng: return ""
    return "▲ 高於範圍" if v > rng[1] else "▼ 低於範圍" if v < rng[0] else "在範圍內"

def advice(m):
    v, r = m["v"], m["ranges"]
    out = []
    if v["vfa_cm2"] > 100:
        out.append(f"**腹部內臟脂肪面積（VFA）**：您的內臟脂肪面積為 {fx(v['vfa_cm2'], 2)} cm²，高於參考值，代謝症候群風險增加")
    if "pbf_pct" in r and v["pbf_pct"] > r["pbf_pct"][1]:
        out.append(f"**體脂率（BFP）**：您的 BFP 為 {fx(v['pbf_pct'], 2)}%，高於建議值，代謝風險提高，宜調整飲食及增加運動")
    return out or ["內臟脂肪面積與體脂率都在參考範圍內（機器在這種情況下的文字未知）。"]

def report_blocks(m, prev, rel):
    """當日報表內容。rel: 從報表所在位置到 body-comp/ 的相對路徑前綴。"""
    v, p = m["v"], m["person"]
    n = m["screen_checked"]
    src = (f"QR＋螢幕照片（{n} 項逐項核對）" if m["screen_verified"] else
           f"QR（之後的螢幕「歷史記錄」核對了 {n} 項；其餘用已驗證的欄位位置）" if n else
           "QR（該次沒有螢幕照片可直接比對；欄位位置已驗證）")
    tc = m["time_check"]
    mt = (f"機器時間 {m['machine_time']}（{tc[2]}；換算後與實際時間吻合）" if tc and tc[0] else
          f"機器時間 {m['machine_time']}（機器時鐘當時不準，僅供參考）")
    age = f"{m['real_age']}" + ("" if m["real_age"] == m["machine_age"] else f"（機器用 {m['machine_age']} 計算）")
    B = [("h1", f"體組成報告 · {m['measured_at']}"),
         ("quote", [f"實際量測時間 **{m['measured_at']}**（{q.TZ_NAME}）· {mt}",
                    f"資料來源：STARBIA 701 {src}"]),
         ("table", ["姓名", "ID", "性別", "年齡", "身高", "檢測時間"],
          [[p["name"], p["id"], p["sex"], age, f"{fx(p['height_cm'])} cm", m["measured_at"]]], "lllllr"),
         ("h2", "體組成分析"),
         ("table", ["項目", "數值", "正常範圍", "判定"],
          [[n, f"{fx(v[k], d)} {u}", f"{fx(m['ranges'][rk][0], d)}–{fx(m['ranges'][rk][1], d)}" if rk in m["ranges"] else "",
            judge(v[k], m["ranges"].get(rk))] for n, k, u, rk, d in COMP_ROWS], "lrrl"),
         ("note", "正常範圍是機器每次量測給的（括號值），每次都不一樣。"),
         ("h2", "體組成評估"), ("svg", "assessment", svg_assessment(m)),
         ("h2", "骨健康與肌肉評估"), ("svg", "bone", svg_bone(m)),
         ("note", "x 軸年齡、y 軸 YA T-score。藍帶 = 同齡參考 ±1（依螢幕描繪，約略）；底色 ≥ −2 綠、−2~−3 黃、−3~−4 橘、< −4 紅。點的位置用實際年齡。"),
         ("svg", "risk", svg_risk(m)),
         ("h2", "主要指標"), ("svg", "scores", svg_scores(m)),
         ("note", "評分已用三天的螢幕照片確認；相位角評分前標「≈」是推定值（±0.07）；骨骼肌評分 QR 裡沒有，所以不畫長條（見文末可信度說明）。"),
         ("h2", "身體組成比例"), ("svg", "composition", svg_composition(m)),
         ("h2", "各肢段"), ("svg", "segments", svg_segments(m)),
         ("table", ["肢段", "肌肉 kg", "評分", "體脂肪 kg", "評分", "骨礦物 kg", "評分"],
          [[s] + sum(([fx(m["segments"][s][f"{k}_kg"], 2), sg(m["segments"][s][f"{k}_score"])] for k in ("muscle", "fat", "bone")), [])
           for s in SEG_ORDER], "lrrrrrr"),
         ("h2", "其他數值"),
         ("table", ["項目", "數值", "備註"], [[n, f"{fx(v[k], d)} {u}".strip(), note] for n, k, u, d, note in OTHER_ROWS], "lrl"),
         ("h2", "檢測結果分析與建議"), ("list", advice(m)), ("note", "文字照機器報告的說法，依數值自動產生。")]
    if prev:
        rows = []
        for k in ("weight_kg", "pbf_pct", "fat_kg", "vfa_cm2", "smm_kg", "muscle_kg", "ffm_kg", "bmd_gcm2", "tbw_kg", "phase_angle"):
            n, u, d, _ = TREND[k]
            dt, word = delta_text(k, prev["v"][k], v[k])
            rows.append([n, f"{fx(prev['v'][k], d)} {u}".strip(), f"{fx(v[k], d)} {u}".strip(), dt, word])
        B += [("h2", f"與上次比較（{prev['measured_at']}）"),
              ("table", ["項目", "上次", "這次", "變化", ""], rows, "lrrrl"),
              ("note", "改善／變差是本 repo 依指標方向自訂的判斷（體重、脂肪、內臟脂肪越低越好；肌肉越高越好）。"
                       "骨密度、相位角短期不會真的改變，單次差異多半是量測誤差，不判斷。單次變化在「波動」之內都不用在意，"
                       "趨勢請看[歷史報表](../history.md)。")]
    B += [("h2", "可信度說明"), ("list", [f"**{a}**：{b}".replace("](../", f"]({rel}") for a, b in CONF_NOTE])]
    return B

def trend_section():
    """趨勢圖分組；有範圍圖的指標一列兩張（一般尺度＋對照正常範圍），其他的兩個一列。"""
    out = []
    for title, note, keys in TREND_GROUPS:
        out += [("h3", title), ("note", note)]
        solo = []
        for k in keys:
            if k in RANGE_CHART: out.append(("charts", [(k, TREND[k][0]), (f"{k}_range", f"{TREND[k][0]}（對照正常範圍）")]))
            else: solo.append((k, TREND[k][0]))
        out += [("charts", solo[i:i + 2]) for i in range(0, len(solo), 2)]
    assert sorted(k for *_, ks in TREND_GROUPS for k in ks) == sorted(TREND), "每個趨勢指標都要分到一組"
    return out

def history_blocks(ms, rel):
    rows = []
    for m in reversed(ms):
        v = m["v"]
        rows.append([f"[{m['measured_at']}]({rel}reports/{m['id']}.md)", fx(v["weight_kg"]), fx(v["pbf_pct"], 2),
                     fx(v["smm_kg"], 2), fx(v["vfa_cm2"], 2), fx(v["bmd_gcm2"], 2),
                     f"✅ {m['screen_checked']} 項" if m["screen_verified"] else f"部分（{m['screen_checked']} 項）" if m["screen_checked"] else "—"])
    full_cols = [k for k, *_ in TRENDS] + ["essential_fat_kg", "storage_fat_kg", "icw_kg", "ecw_kg", "ecm_kg",
                                          "body_cell_mass_kg", "bmc_kg", "ffm_pct", "asmi", "upper_limb_muscle_kg",
                                          "lower_limb_muscle_kg", "weight_pct_of_std", "bmd_tscore"]
    names = {k: f"{n}{f' ({u})' if u else ''}" for k, n, u, *_ in TRENDS}
    names.update({"essential_fat_kg": "必須脂肪 (kg)", "storage_fat_kg": "儲存脂肪 (kg)", "icw_kg": "細胞內液 (kg)",
                  "ecw_kg": "細胞外液 (kg)", "ecm_kg": "細胞外質量 (kg)", "body_cell_mass_kg": "身體細胞質量 (kg)",
                  "bmc_kg": "骨礦物質 (kg)", "ffm_pct": "去脂質量 (%)", "asmi": "ASMI (kg/m²)",
                  "upper_limb_muscle_kg": "上肢肌肉量 (kg)", "lower_limb_muscle_kg": "下肢肌肉量 (kg)",
                  "weight_pct_of_std": "體重 (標準%)", "bmd_tscore": "骨密度 T-score"})
    dec = lambda k: TREND[k][2] if k in TREND else 2
    full = [[m["measured_at"], m["machine_time"][5:]] + [fx(m["v"][k], dec(k)) for k in full_cols] for m in reversed(ms)]
    return [("h1", "體組成歷史"),
            ("quote", [f"共 {len(ms)} 次量測 · 最新 {ms[-1]['measured_at']} · 時間一律是實際量測時間（{q.TZ_NAME}）"]),
            ("h2", "每次量測"),
            ("table", ["量測時間（點進去看當日報表）", "體重", "體脂率", "骨骼肌", "內臟脂肪面積", "骨密度", "螢幕核對"], rows, "lrrrrrc"),
            ("h2", "趨勢"),
            ("note", "每個指標依性質用不同畫法（見各組說明）。有正常範圍的指標多一張「對照正常範圍」的圖（右邊）。"
                     "GitHub 上的圖不能互動；滑鼠移到點上看日期與數值請下載 dashboard.html 用瀏覽器開。"),
            *trend_section(),
            ("h2", "完整數值"),
            ("table", ["量測時間", "機器時間"] + [names[k] for k in full_cols], full, "l" + "r" * (len(full_cols) + 1))]

# ---------------- 輸出：Markdown ----------------
def md_cell(s): return str(s).replace("|", "\\|").replace("\n", " ")

def to_md(blocks, svg_dir, svg_rel):
    out = []
    for b in blocks:
        k = b[0]
        if k == "h1": out.append(f"# {b[1]}\n")
        elif k == "h2": out.append(f"## {b[1]}\n")
        elif k == "quote": out.append("\n".join(f"> {line}  " for line in b[1]) + "\n")
        elif k == "note": out.append(f"<sub>{b[1]}</sub>\n")
        elif k == "list": out.append("\n".join(f"- {x}" for x in b[1]) + "\n")
        elif k == "table":
            heads, rows, align = b[1], b[2], b[3]
            al = {"l": ":--", "r": "--:", "c": ":-:"}
            out.append("| " + " | ".join(md_cell(h) for h in heads) + " |\n|" + "|".join(al[a] for a in align) + "|\n" +
                       "\n".join("| " + " | ".join(md_cell(c) for c in r) + " |" for r in rows) + "\n")
        elif k == "svg":
            name, content = b[1], b[2]
            os.makedirs(svg_dir, exist_ok=True)
            with open(os.path.join(svg_dir, f"{name}.svg"), "w", encoding="utf-8") as f: f.write(content)
            out.append(f"![{name}]({svg_rel}{name}.svg)\n")
        elif k == "h3": out.append(f"### {b[1]}\n")
        elif k == "charts":
            out.append(" ".join(f"![{alt}]({svg_rel}{name}.svg)" for name, alt in b[1]) + "\n")
    return "\n".join(out)

# ---------------- 輸出：HTML（選用） ----------------
def md_inline_html(s):
    s = escape(str(s))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", s)

def to_html(blocks, trend_svgs):
    out = []
    for b in blocks:
        k = b[0]
        if k == "h1": out.append(f"<h1>{escape(b[1])}</h1>")
        elif k == "h2": out.append(f"<h2>{escape(b[1])}</h2>")
        elif k == "h3": out.append(f"<h3>{escape(b[1])}</h3>")
        elif k == "quote": out.append("<p class=q>" + "<br>".join(md_inline_html(x) for x in b[1]) + "</p>")
        elif k == "note": out.append(f"<p class=n>{md_inline_html(b[1])}</p>")
        elif k == "list": out.append("<ul>" + "".join(f"<li>{md_inline_html(x)}</li>" for x in b[1]) + "</ul>")
        elif k == "table":
            heads, rows, align = b[1], b[2], b[3]
            a = {"l": "left", "r": "right", "c": "center"}
            out.append("<div class=s><table><thead><tr>" + "".join(f'<th style="text-align:{a[al]}">{md_inline_html(h)}</th>' for h, al in zip(heads, align)) +
                       "</tr></thead><tbody>" + "".join("<tr>" + "".join(f'<td style="text-align:{a[al]}">{md_inline_html(c)}</td>' for c, al in zip(r, align)) + "</tr>" for r in rows) +
                       "</tbody></table></div>")
        elif k == "svg": out.append(f"<div class=g>{b[2]}</div>")
        elif k == "charts": out.append("<div class=t>" + "".join(trend_svgs[name] for name, _ in b[1]) + "</div>")
    return "\n".join(out)

HTML_PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>體組成報告</title>
<style>
  :root { color-scheme: light dark; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e; --grid:#e1e0d9; --wash:#f0efec; }
  @media (prefers-color-scheme: dark) { :root { --page:#0d0d0d; --ink:#f0efec; --ink2:#c3c2b7; --grid:#2c2c2a; --wash:#262624; } }
  body { margin:0; background:var(--page); color:var(--ink); font:14px/1.6 -apple-system,"Segoe UI","Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif; }
  main { max-width:900px; margin:0 auto; padding:16px; }
  nav { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:8px; }
  select { font:inherit; padding:6px 8px; border-radius:8px; }
  h1 { font-size:20px; } h2 { font-size:16px; margin-top:28px; } h3 { font-size:14px; margin:20px 0 4px; }
  .q { background:var(--wash); padding:8px 12px; border-radius:8px; color:var(--ink2); }
  .n { color:var(--ink2); font-size:12px; }
  .s { overflow-x:auto; } table { border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
  th, td { padding:5px 10px; border-bottom:1px solid var(--grid); white-space:nowrap; } th { color:var(--ink2); font-weight:600; }
  .g svg, .t svg { max-width:100%; height:auto; } .t { display:flex; flex-wrap:wrap; gap:8px; }
  section[hidden] { display:none; }
  #tip { position:fixed; z-index:9; pointer-events:none; display:none; background:var(--page); color:var(--ink);
         border:1px solid var(--grid); border-radius:8px; padding:6px 10px; font-size:12px; box-shadow:0 4px 14px rgba(0,0,0,.15); }
  svg g:has(> title) { cursor:pointer; }
</style></head><body><main>
<nav><b>體組成報告</b><select id="pick">__OPTIONS__</select></nav>
__SECTIONS__
</main>
<script>
  const pick = document.getElementById("pick");
  const show = () => document.querySelectorAll("section").forEach((s) => { s.hidden = s.id !== pick.value; });
  pick.addEventListener("change", show); show();
  // 圖上的點：滑過（手機：點一下）立刻顯示日期與數值（內容來自 SVG 的 <title>）
  const tip = document.createElement("div"); tip.id = "tip"; document.body.append(tip);
  const hide = () => { tip.style.display = "none"; };
  const at = (e) => {
    const g = e.target.closest("g");
    const ti = g && g.querySelector(":scope > title");
    if (!ti) { if (e.type === "pointerdown") hide(); return; }
    tip.textContent = ti.textContent; tip.style.display = "block";
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(innerWidth - r.width - 8, Math.max(8, e.clientX + 12)) + "px";
    tip.style.top = Math.max(8, e.clientY - r.height - 12) + "px";
  };
  document.addEventListener("pointerover", at); document.addEventListener("pointermove", at);
  document.addEventListener("pointerdown", at); document.addEventListener("scroll", hide, { passive: true });
  document.querySelectorAll("svg g > title").forEach((ti) => ti.parentNode.setAttribute("aria-label", ti.textContent));
</script></body></html>
"""

# ---------------- 主程式 ----------------
def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def update_readme(ms):
    if os.environ.get("BODYCOMP_DIR") or not os.path.exists(README): return
    s = open(README, encoding="utf-8").read()
    if not ms:
        block = "<!-- latest:start -->\n（還沒有量測。先完成下面的「第一次設定」，量完上傳第一張 QR code 後，這裡會自動顯示最新一次的結果與趨勢圖）\n<!-- latest:end -->"
        new = re.sub(r"<!-- latest:start -->.*?<!-- latest:end -->", lambda _: block, s, flags=re.S)
        if new != s: write(README, new)
        return
    m = ms[-1]
    v = m["v"]
    block = (f"<!-- latest:start -->\n最新量測：**[{m['measured_at']}](body-comp/reports/{m['id']}.md)**"
             f"（體重 {fx(v['weight_kg'])} kg · 體脂率 {fx(v['pbf_pct'], 2)}% · 骨骼肌 {fx(v['smm_kg'], 2)} kg · "
             f"內臟脂肪面積 {fx(v['vfa_cm2'], 2)} cm²）· [歷史報表](body-comp/history.md)\n\n" +
             " ".join(f"![{TREND[k][0]}](body-comp/charts/{k}.svg)" for k in ("weight_kg", "pbf_pct", "smm_kg", "vfa_cm2")) +
             "\n<!-- latest:end -->")
    new = re.sub(r"<!-- latest:start -->.*?<!-- latest:end -->", lambda _: block, s, flags=re.S)
    if new != s: write(README, new)

def main():
    ms = load()
    if not ms:                                     # 剛建立的 repo，或把量測都刪了：清掉舊的產出
        for d in (REPORTS, CHARTS): shutil.rmtree(d, ignore_errors=True)
        for f in (HISTORY, DASHBOARD):
            if os.path.exists(f): os.remove(f)
        update_readme(ms)
        print(f"還沒有量測資料（{os.path.relpath(ROOT, REPO)}/raw/ 是空的），不產生報表"); return 0
    for d in (REPORTS, CHARTS):
        shutil.rmtree(d, ignore_errors=True)
    trend_svgs = {k: svg_trend(ms, k) for k, *_ in TRENDS}
    trend_svgs.update({f"{k}_range": svg_range(ms, k) for k in RANGE_CHART})
    for k, s in trend_svgs.items():
        write(os.path.join(CHARTS, f"{k}.svg"), s)
    sections, options = [], []
    for i, m in enumerate(ms):
        prev = ms[i - 1] if i else None
        blocks = report_blocks(m, prev, "../")
        write(os.path.join(REPORTS, f"{m['id']}.md"),
              to_md(blocks, os.path.join(REPORTS, m["id"]), f"{m['id']}/") +
              f"\n---\n[← 歷史報表](../history.md) · 由 `scripts/report.py` 產生，請勿手動編輯\n")
        sections.append((m["id"], to_html(blocks, trend_svgs)))
        options.append((m["id"], m["measured_at"] + ("（有螢幕照片）" if m["screen_verified"] else "")))
    hb = history_blocks(ms, "")
    write(HISTORY, to_md(hb, CHARTS, "charts/") + "\n---\n由 `scripts/report.py` 產生，請勿手動編輯\n")
    sections.append(("history", to_html(hb, trend_svgs)))
    options.append(("history", "歷史報表"))
    page = HTML_PAGE.replace("__OPTIONS__", "".join(f'<option value="{i}">{escape(t_)}</option>' for i, t_ in reversed(options[:-1])) +
                             f'<option value="history">歷史報表</option>')
    page = page.replace("__SECTIONS__", "\n".join(f'<section id="{i}">{h}</section>' for i, h in sections))
    write(DASHBOARD, page)
    update_readme(ms)
    import verify_screen
    bad = verify_screen.main(quiet=True)
    for m in ms:                                   # 欄位關係檢查（CI 也會因此失敗）
        for x in q.consistency_issues(open(q.raw_path(m["measured_at"]), encoding="utf-8").read().strip().split(",")):
            print(f"⚠ {m['measured_at']} QR 欄位關係不成立：{x}"); bad += 1
    print(f"→ {len(ms)} 份當日報表、history.md、charts/、dashboard.html" + ("" if not bad else f"；⚠ 螢幕驗證有 {bad} 項不一致"))
    return bad

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
