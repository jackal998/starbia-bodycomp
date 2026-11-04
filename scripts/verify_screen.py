# -*- coding: utf-8 -*-
"""把螢幕照片上抄下來的數值（raw/<日期>/screen_values.csv）跟 QR 解出的值逐項比對，
寫出 raw/<日期>/VERIFY.md。有任何一項不一致就回傳非 0（CI 會擋下來）。

  python scripts/verify_screen.py

screen_values.csv 欄位：
  measured_at  哪一次量測（實際時間）
  page / item  螢幕哪一頁、哪一項
  shown        螢幕上顯示的文字（照抄，包含小數位數）
  ref          對應的值：v.<欄位> person.<欄位> range.<指標>.<0|1> seg.<肢段>.<欄位>
               score.<評分> d.<推算值> machine_age machine_time；空白 = QR 裡還沒對出
  kind         num：數字，依螢幕的小數位數比對（差距 ≤ 半個末位）
               text：文字完全相同
               bar：長條或圖上點位的量測讀值，容許 ±tol（預設 0.1）
  tol          （選填）bar 的容許誤差；依量法的實測精度給（見各列 note）
"""
import glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import starbia_qr as q

BAR_TOL = 0.1

def lookup(m, ref):
    head, _, rest = ref.partition(".")
    if head == "v": return m["v"][rest]
    if head == "d": return m["d"][rest]
    if head == "person": return m["person"][rest]
    if head == "machine_age": return m["machine_age"]
    if head == "machine_time": return m["machine_time"]
    if head == "range":
        k, i = rest.rsplit(".", 1); return m["ranges"][k][int(i)]
    if head == "seg":
        s, k = rest.split("."); return m["segments"][s][k]
    if head == "score": return m["scores"][rest][0]
    raise KeyError(ref)

def check(m, row):
    shown, ref, kind = row["shown"], row["ref"], row["kind"]
    if not ref:
        return "⚪", "—", "" if row.get("note") else "QR 欄位未對出"
    val = lookup(m, ref)
    if kind == "text":
        ok = str(val) == shown
        return ("✅" if ok else "❌"), str(val), ""
    v = float(val)
    if kind == "bar":
        diff = v - float(shown)
        tol = float(row.get("tol") or BAR_TOL)
        return ("🟡" if abs(diff) <= tol + 1e-9 else "❌"), f"{v:g}", f"讀值差 {diff:+.3f}（容許 ±{tol:g}）"
    dec = len(shown.split(".")[1]) if "." in shown else 0
    diff = abs(v - float(shown))
    ok = diff <= 0.5 * 10 ** -dec + 1e-9
    note = "QR 為 x.x5，機器內部值決定進位方向" if ok and abs(diff - 0.5 * 10 ** -dec) < 1e-9 and dec else ""
    return ("✅" if ok else "❌"), f"{v:g}", note

def counts_by_measurement():
    """每次量測被螢幕核對過幾項（✅＋🟡），給報表標示資料來源用。"""
    out = {}
    for path in glob.glob(os.path.join(q.ROOT, "raw", "*", "screen_values.csv")):
        for r in q.read_csv(path):
            if r["ref"]: out[r["measured_at"]] = out.get(r["measured_at"], 0) + 1
    return out

def main(quiet=False):
    import report
    ms = {m["measured_at"]: m for m in report.load()}
    bad = 0
    for path in sorted(glob.glob(os.path.join(q.ROOT, "raw", "*", "screen_values.csv"))):
        rows = q.read_csv(path)
        lines, counts = [], {}
        for r in rows:
            m = ms.get(r["measured_at"])
            if not m:
                mark, got, note = "❌", "—", "找不到這次量測"
            else:
                mark, got, note = check(m, r)
            counts[mark] = counts.get(mark, 0) + 1
            bad += mark == "❌"
            extra = "；".join(x for x in (note, r.get("note", "")) if x)
            who = "" if r["measured_at"] == rows[0]["measured_at"] else f"〔{r['measured_at']}〕"
            lines.append(f"| {r['page']} | {who}{r['item']} | {r['shown']} | {got} | {mark} | {extra} |")
        day = os.path.basename(os.path.dirname(path))
        out = [f"# 螢幕 vs QR 驗證 · {day}", "",
               "螢幕照片上每個看得到的數值（`screen_values.csv`，人工抄錄）與 QR 解出的值逐項比對。",
               "由 `scripts/verify_screen.py` 產生；CI 每次都重跑，任何 ❌ 會讓檢查失敗。", "",
               "- ✅ 一致：依螢幕的小數位數四捨五入後相同（差距 ≤ 半個末位）",
               "- 🟡 長條／圖點：螢幕只有長條或圖上的點，量測位置換算的讀值；容許誤差依量法的實測精度（備註欄）",
               "- ⚪ 未對出：螢幕上有，但 QR 裡還找不到唯一對應的欄位",
               "- ❌ 不一致", "",
               "**統計：** " + "、".join(f"{k} {v}" for k, v in sorted(counts.items())), "",
               "這頁也包含螢幕上其他次量測的數值（例如「體組成歷史記錄」），以 `measured_at` 對到該次量測。", "",
               "| 頁面 | 項目 | 螢幕 | QR／計算 | 結果 | 備註 |", "|:--|:--|--:|--:|:-:|:--|", *lines, "",
               "QR 有、但螢幕上沒有的欄位（無法用螢幕驗證）：BMI（= 體重 ÷ 身高² 吻合）、"
               "基礎代謝率（= 370 + 21.6 × 去脂質量 吻合）、ECW/TBW（自行計算）。", ""]
        with open(os.path.join(os.path.dirname(path), "VERIFY.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        if not quiet:
            print(f"{day}: " + "、".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return bad

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
