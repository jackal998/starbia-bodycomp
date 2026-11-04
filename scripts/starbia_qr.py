# -*- coding: utf-8 -*-
"""STARBIA 701「掃描報告結果」QR → measurements.csv 一列 + ranges.csv 該次的正常範圍。

QR 內容 = base64( gzip( CSV 一行, 222 欄 ) )，最後一欄是廠商的加密/簽章資料。
欄位對照與可信度：FIELDS / RANGES / SEGMENTS / SCORES 都用機器螢幕照片逐項核對過
（方法見 docs/VALIDATION.md；scripts/verify_screen.py 可以用你自己的螢幕照片重跑）。其餘欄位意義不明，
原始整行另存在 raw/<日期>/qr_<HHMM>.csv，raw/ 是唯一的原始資料，CSV 都可由它重建。

⚠ 機器時鐘不能信：離線的機器開機後時鐘可能回到很久以前，所以一律以「實際量測時間」為準。

資料夾預設是 repo 裡的 body-comp/，可用環境變數 BODYCOMP_DIR 改（示範資料就是 BODYCOMP_DIR=demo）。

  python scripts/starbia_qr.py <QR 照片 | qr 文字檔 | raw 的 qr_*.csv> [--at "2026-01-15 18:30"]   # 預覽
  python scripts/starbia_qr.py <...> --at "..." --apply                                          # 寫入
  python scripts/starbia_qr.py --rebuild          # 由 raw/*/qr_*.csv 重建 measurements.csv、ranges.csv

平常不用自己跑：在 GitHub 開「新增量測」issue 上傳 QR 照片即可（.github/workflows/measurement.yml）。
讀照片需要 `pip install zxing-cpp pillow`；只給 .txt（QR 解出的文字）則只用標準庫。
"""
import base64, binascii, csv, glob, gzip, hashlib, os, re, sys, zlib
from datetime import date

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = os.path.join(REPO_ROOT, os.environ.get("BODYCOMP_DIR") or "body-comp")
TZ_HOURS, TZ_NAME = 8, "台灣時間"          # 實際量測時間的時區（issue 建立時間換算、機器時鐘比對都用它）
CSV_PATH = os.path.join(ROOT, "measurements.csv")
RANGES_PATH = os.path.join(ROOT, "ranges.csv")
N_FIELDS = 222

ID, NAME, HEIGHT, SEX, DOB, AGE, MACHINE_TIME = 0, 1, 5, 6, 7, 8, 9      # QR 欄位 index
# csv 欄名: QR 欄位 index。全部對照過螢幕；bmi、bmr 螢幕上沒有，但跟公式吻合
# （bmi = 體重/身高²，bmr = 370 + 21.6 × 去脂質量）。
FIELDS = {
    "weight_kg": 10, "bmi": 12, "pbf_pct": 16, "fat_kg": 15,
    "essential_fat_kg": 207, "storage_fat_kg": 208,
    "ffm_kg": 14, "ffm_pct": 73, "muscle_kg": 18, "smm_kg": 37, "body_cell_mass_kg": 19, "ecm_kg": 189,
    "protein_kg": 52, "tbw_kg": 40, "icw_kg": 38, "ecw_kg": 39,
    "bmc_kg": 17, "bmd_gcm2": 72, "bmd_tscore": 200, "vfa_cm2": 20, "phase_angle": 11, "bmr_kcal": 57,
    "smi": 193, "asmi": 192, "upper_limb_muscle_kg": 171, "lower_limb_muscle_kg": 172,
    "weight_pct_of_std": 106,
}
# ecw_ratio 自己算：QR 第 41 欄看似 ECW/TBW 但和 ECW ÷ TBW 差 0.001，螢幕上也沒有。
COLUMNS = ["measured_at", "machine_time", "machine_age"] + list(FIELDS) + ["ecw_ratio", "qr_id", "source"]

# 正常範圍（螢幕括號值）: metric -> (low index, high index, 中文名)。每次量測都不一樣。
RANGES = {
    "weight_kg": (97, 98, "體重"), "pbf_pct": (93, 94, "體脂率"), "fat_kg": (95, 96, "脂肪質量"),
    "ffm_kg": (88, 89, "去脂質量"), "protein_kg": (121, 122, "蛋白質"), "tbw_kg": (84, 85, "總體水分"),
    "bmc_kg": (118, 119, "骨礦物質"), "body_cell_mass_kg": (86, 87, "身體細胞質量"),
    "ecm_kg": (209, 210, "細胞外質量"),
}
RANGE_COLUMNS = ["measured_at", "metric", "low", "high", "label", "source"]
# 一般參考值（不是機器每次給的）：ranges.csv 裡 measured_at 空白的列，可以手改
GENERAL_RANGES = [("vfa_cm2", "0", "100", "內臟脂肪面積", "一般參考值 (<100 cm²)")]

# ---- 以下只給報表用（scripts/report.py），不寫進 CSV ----
# 各肢段 (kg, 評分) 的 QR index。三組的左右順序都不一樣，已用螢幕逐格核對：
# 15 個評分完全一致、15 個 kg 在螢幕的一位小數內一致。
SEGMENTS = {
    "右上肢": {"muscle": (22, 202), "fat": (211, 217), "bone": (33, 76)},
    "左上肢": {"muscle": (23, 203), "fat": (212, 216), "bone": (32, 75)},
    "軀幹":   {"muscle": (24, 204), "fat": (213, 218), "bone": (36, 77)},
    "右下肢": {"muscle": (25, 206), "fat": (215, 220), "bone": (35, 79)},
    "左下肢": {"muscle": (26, 205), "fat": (214, 219), "bone": (34, 78)},
}
# 整體評分。螢幕上只有長條、沒有數字：量長條末端位置（以軌道外框／刻度校正）後在 QR 裡找吻合的欄位。
# 用三天的螢幕照片量過（方法見 docs/VALIDATION.md）。
#   確認：三天都吻合（誤差在量法精度內）、且是唯一吻合的欄位
#   推定：吻合但有已知的系統性誤差
# 骨骼肌評分在 QR 裡找不到吻合欄位 → 不列（可能是機器顯示時才算）。
SCORES = {   # key: (index, 可信度, 依據)
    "upper_limb_muscle": (197, "確認", "= 左右上肢肌肉評分的平均（作者的每一次量測都成立）"),
    "lower_limb_muscle": (198, "確認", "= 左右下肢肌肉評分的平均（作者的每一次量測都成立）"),
    "trunk_muscle":      (204, "確認", "= 軀幹肌肉評分（同一欄位）"),
    "fat":    (81, "確認", "三天的螢幕長條都在 ±0.02 內吻合"),
    "bmc":    (80, "確認", "三天的螢幕長條都在 ±0.02 內吻合"),
    "muscle": (201, "確認", "三天的螢幕長條都在 ±0.01 內吻合"),
    "tbw":    (90, "確認", "三天的螢幕長條都在 ±0.02 內吻合"),
    "smi":    (82, "確認", "三天的螢幕長條（扣除圓角偏差）都在 ±0.02 內吻合"),
    "asmi":   (199, "確認", "三天的螢幕長條（扣除圓角偏差）都在 ±0.02 內吻合，且與 ASMI 數值成線性"),
    "pa":     (83, "推定", "≈ 1.4 ×（相位角取一位小數 − 6.4），末位可能差 0.01（十多次量測都成立）；螢幕長條用未進位的相位角，差最多 ±0.07"),
}
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

# ---------------- 解碼 ----------------
def decode_payload(text):
    """QR 文字 → 222 欄 list；不是 STARBIA 報告 QR 就回傳 None。"""
    try:
        row = gzip.decompress(base64.b64decode("".join(text.split()), validate=True)).decode("utf-8").split(",")
    except (binascii.Error, OSError, EOFError, zlib.error, UnicodeDecodeError, ValueError):
        return None
    if len(row) < 20 or not TIME_RE.match(row[MACHINE_TIME][:16]):
        return None
    return row

def rows_from_image(im):
    """PIL 影像裡所有 STARBIA QR 解出的列（去重）。"""
    import zxingcpp
    from PIL import ImageOps
    im = ImageOps.exif_transpose(im).convert("L")
    out = []
    for scale in (1, 0.5, 0.25):
        src = im if scale == 1 else im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))))
        for r in zxingcpp.read_barcodes(src, try_rotate=True, try_downscale=True):
            row = decode_payload(r.text)
            if row and row not in out:
                out.append(row)
        if out:
            break
    return out

def parse(path):
    if path.lower().endswith(".csv"):        # raw/ 裡已解開的原始列
        return open(path, encoding="utf-8").read().strip().split(",")
    if path.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")):
        from PIL import Image
        rows = rows_from_image(Image.open(path))
        if not rows: sys.exit(f"讀不到 STARBIA QR: {path}")
        return rows[0]
    row = decode_payload(open(path, encoding="utf-8").read())
    if not row: sys.exit(f"不是 STARBIA QR 內容: {path}")
    return row

def photo_time(im):
    """照片 EXIF 拍攝時間（DateTimeOriginal），沒有就 None。"""
    try:
        t = im.getexif().get_ifd(0x8769).get(0x9003)
    except Exception:
        return None
    return f"{t[:10].replace(':', '-')} {t[11:16]}" if t and TIME_RE.match(f"{t[:10].replace(':', '-')} {t[11:16]}") else None

def strip_gps(data):
    """照片 bytes → 去掉 EXIF 的 GPS 定位（保留拍攝時間等其他欄位）。不是 JPEG／沒有 GPS 就原樣回傳。
    JPEG 以 quality="keep" 重存（沿用原本的量化表），畫質幾乎不變、QR 仍可讀。"""
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        if im.format != "JPEG": return data
        ex = im.getexif()
        if 0x8825 not in ex: return data
        ex.get_ifd(0x8769)                      # 先載入 Exif 子 IFD，tobytes 才會一起寫回
        del ex[0x8825]
        out = io.BytesIO()
        im.save(out, "JPEG", quality="keep", subsampling="keep", exif=ex.tobytes(),
                icc_profile=im.info.get("icc_profile"))
        return out.getvalue()
    except Exception:
        return data

# ---------------- 紀錄 ----------------
def machine_time_check(raw, when):
    """實際時間 vs 機器時間：吻合就是交叉驗證，差很多代表機器時鐘又錯了或實際時間填錯。
    機器時鐘大多是 UTC，偶爾是本地時間，兩種都接受。
    回傳 (是否吻合, 說明, 時區標籤)；機器時間格式不對就 None。"""
    from datetime import datetime, timedelta
    try:
        mt = datetime.strptime(raw[MACHINE_TIME][:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    real = datetime.strptime(when, "%Y-%m-%d %H:%M")
    for hours, label in ((TZ_HOURS, "UTC"), (0, TZ_NAME)):
        local = mt + timedelta(hours=hours)
        diff = abs((real - local).total_seconds()) / 60
        if diff <= 15:
            return True, f"機器時間（{label}）換算{TZ_NAME} {local:%Y-%m-%d %H:%M}，與實際時間相差 {diff:.0f} 分鐘（吻合）", label
    local = mt + timedelta(hours=TZ_HOURS)
    return (False, f"機器時間不論當 UTC 或{TZ_NAME}都對不上（當 UTC 換算為 {local:%Y-%m-%d %H:%M}，"
                   f"差 {(real - local).total_seconds() / 3600:+.1f} 小時）— 機器時鐘不準，或實際時間填錯", None)

def age_on(dob, day):
    b, d = date.fromisoformat(dob), date.fromisoformat(day)
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))

def qr_id(raw):
    return hashlib.sha1(",".join(raw).encode()).hexdigest()[:12]

def record(raw, when):
    rec = {"measured_at": when, "machine_time": raw[MACHINE_TIME], "machine_age": raw[AGE]}
    rec.update({k: raw[i] for k, i in FIELDS.items()})
    rec["ecw_ratio"] = f"{float(rec['ecw_kg']) / float(rec['tbw_kg']):.3f}"
    rec["qr_id"] = qr_id(raw)
    rec["source"] = "STARBIA 701 QR"
    return rec

def range_rows(rec, raw):
    return [{"measured_at": rec["measured_at"], "metric": m, "low": raw[lo], "high": raw[hi],
             "label": label, "source": "STARBIA 701 QR"} for m, (lo, hi, label) in RANGES.items()]

def consistency_issues(raw):
    """QR 欄位之間的固定關係（作者的每一次量測都成立）。不成立代表機器韌體或 QR 格式變了，欄位對照可能失效。"""
    try:
        f = lambda i: float(raw[i]); h = float(raw[HEIGHT]) / 100
        w, ffm, fat = f(10), f(14), f(15)
        rules = [
            ("體重 = 去脂質量 + 脂肪質量", abs(w - (ffm + fat)) < 0.06),
            ("脂肪質量 = 必須脂肪 + 儲存脂肪", abs(fat - (f(207) + f(208))) < 0.02),
            ("總體水分 = 細胞內液 + 細胞外液", abs(f(40) - (f(38) + f(39))) < 0.02),
            ("體脂率 = 脂肪 ÷ 體重", abs(f(16) - fat / w * 100) < 0.1),
            ("BMI = 體重 ÷ 身高²", abs(f(12) - w / h ** 2) < 0.05),
            ("基礎代謝率 = 370 + 21.6 × 去脂質量", abs(f(57) - (370 + 21.6 * ffm)) < 1.5),
            ("上肢肌肉 = 左右上肢", abs(f(171) - (f(22) + f(23))) < 0.02),
            ("下肢肌肉 = 左右下肢", abs(f(172) - (f(25) + f(26))) < 0.02),
            ("SMI = 骨骼肌 ÷ 身高²", abs(f(193) - f(37) / h ** 2) < 0.01),
            ("ASMI = 四肢肌肉 ÷ 身高²", abs(f(192) - (f(171) + f(172)) / h ** 2) < 0.01),
            ("上肢評分 = 兩臂評分平均", abs(f(197) - (f(202) + f(203)) / 2) < 0.006),
            ("下肢評分 = 兩腿評分平均", abs(f(198) - (f(205) + f(206)) / 2) < 0.006),
        ]
    except (ValueError, IndexError) as e:
        return [f"QR 欄位無法解析（{type(e).__name__}）"]
    return [name for name, good in rules if not good]

def warnings_for(raw, when):
    out = []
    if len(raw) != N_FIELDS:
        out.append(f"QR 欄位數 {len(raw)}（預期 {N_FIELDS}）— 機器版本可能不同，欄位對照可能失效")
    out += [f"QR 欄位關係不成立：{x} — 機器韌體或 QR 格式可能變了，欄位對照要重新驗證" for x in consistency_issues(raw)]
    real = age_on(raw[DOB], when[:10])
    if str(real) != raw[AGE]:
        out.append(f"機器用 {raw[AGE]} 歲計算（它的時鐘是錯的），實際 {real} 歲 — 年齡相關的標準值可能有偏差")
    return out

def read_csv(path):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8") as f: return list(csv.DictReader(f))

def write_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, columns, lineterminator="\n"); w.writeheader(); w.writerows(rows)

def duplicate_of(raw, when):
    """已存在的 measured_at（同一個 QR 或同一時間），沒有就 None。"""
    i = qr_id(raw)
    for r in read_csv(CSV_PATH):
        if r["qr_id"] == i or r["measured_at"] == when:
            return r["measured_at"]
    return None

def raw_path(when):
    day, t = when.split()
    return os.path.join(ROOT, "raw", day, f"qr_{t.replace(':', '')}.csv")

def add_measurement(raw, when):
    """寫 raw/<日期>/qr_<HHMM>.csv，再由 raw/ 重建 CSV。回傳寫入的紀錄。"""
    if not TIME_RE.match(when): raise ValueError(f"時間格式要是 YYYY-MM-DD HH:MM: {when}")
    dup = duplicate_of(raw, when)
    if dup: raise ValueError(f"已存在：{dup}")
    p = raw_path(when)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as g: g.write(",".join(raw) + "\n")
    rebuild()
    return record(raw, when)

def rebuild():
    """raw/<日期>/qr_<HHMM>.csv 是唯一來源：重寫 measurements.csv，ranges.csv 保留一般參考值列
    （沒有就補上預設的 GENERAL_RANGES）。"""
    os.makedirs(ROOT, exist_ok=True)
    rngs = [r for r in read_csv(RANGES_PATH) if not r["measured_at"]]
    have = {r["metric"] for r in rngs}
    rngs += [dict(zip(RANGE_COLUMNS, ["", *g])) for g in GENERAL_RANGES if g[0] not in have]
    recs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "raw", "*", "qr_*.csv"))):
        day = os.path.basename(os.path.dirname(p))
        hhmm = os.path.basename(p)[3:7]
        when = f"{day} {hhmm[:2]}:{hhmm[2:]}"
        raw = open(p, encoding="utf-8").read().strip().split(",")
        rec = record(raw, when)
        recs.append(rec); rngs += range_rows(rec, raw)
    write_csv(CSV_PATH, COLUMNS, recs)
    write_csv(RANGES_PATH, RANGE_COLUMNS, rngs)
    return recs

def main(argv):
    if argv == ["--rebuild"]:
        print(f"重建 {len(rebuild())} 筆 → {CSV_PATH}、{RANGES_PATH}"); return
    apply = "--apply" in argv
    at = None
    if "--at" in argv:
        i = argv.index("--at"); at = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
        if not TIME_RE.match(at):
            sys.exit(f'--at 格式要是 "YYYY-MM-DD HH:MM"（{TZ_NAME}）: {at}')
    paths = [a for a in argv if a != "--apply"]
    if not paths:
        sys.exit(__doc__)
    if at and len(paths) > 1:
        sys.exit("--at 一次只能配一個 QR")
    for p in paths:
        raw = parse(p)
        when = at
        if not when and p.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")):
            from PIL import Image
            when = photo_time(Image.open(p))
        rec = record(raw, when or "?")
        print("  ".join(f"{k}={v}" for k, v in rec.items()))
        if not when:
            print('  ✗ 不知道實際量測時間 — 照片沒有 EXIF，請加 --at "YYYY-MM-DD HH:MM"'); continue
        for w in warnings_for(raw, when): print("  ⚠", w)
        if not apply: continue
        try:
            add_measurement(raw, when); print(f"  寫入 → {raw_path(when)}")
        except ValueError as e:
            print(f"  SKIP {e}")

if __name__ == "__main__":
    main(sys.argv[1:])
