# -*- coding: utf-8 -*-
"""示範資料：一個虛構的人（「示範」，DEMO），幾週的合成量測 → 跟真的 QR 同格式的原始列。

  python scripts/demo.py           重新產生 demo/（原始資料、CSV、報表）與 examples/demo_qr.png
  python scripts/demo.py --check   自我測試（CI 跑這個）：在暫存資料夾裡用 0、1、2、多筆量測各跑一遍
                                   「產生 QR 圖 → 讀回 → 寫入 → 重建 → 報表」，任何一步不對就失敗

數值全部是亂數合成（固定種子），不是任何人的量測。只有 starbia_qr.py 用到的欄位有意義，
而且彼此滿足 consistency_issues() 的關係（體重 = 去脂 + 脂肪、BMI = 體重 ÷ 身高² 等）；
其他欄位一律 0，最後一欄（廠商簽章）是假的。
"""
import base64, gzip, os, random, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
QR_PNG = os.path.join(REPO, "examples", "demo_qr.png")

PERSON = {"id": "DEMO", "name": "示範", "height": 172.0, "sex": "1", "dob": "1990-03-15"}
START = datetime(2026, 1, 5, 18, 30)       # 第一次量測（本地時間）

def r2(v): return round(v, 2)

def measurement(i, rnd, day):
    """第 i 次量測的原始列（222 欄字串）。趨勢：體重、脂肪慢慢下降，肌肉大致持平，加上 BIA 等級的雜訊。"""
    import starbia_qr as q
    h = PERSON["height"] / 100
    w = round(78.0 - 0.09 * day + rnd.gauss(0, 0.35), 1)
    pbf = 27.0 - 0.05 * day + rnd.gauss(0, 0.5)
    fat = r2(w * pbf / 100)
    ffm = r2(w - fat)
    pbf = r2(fat / w * 100)
    ess = r2(0.03 * w); storage = r2(fat - ess)
    tbw = r2(0.732 * ffm + rnd.gauss(0, 0.1)); icw = r2(0.62 * tbw); ecw = r2(tbw - icw)
    bmc = r2(0.052 * ffm); protein = r2(ffm - tbw - bmc)
    muscle = r2(ffm - bmc + rnd.gauss(0, 0.05)); smm = r2(0.63 * ffm + rnd.gauss(0, 0.1))
    bcm = r2(0.66 * ffm); ecm = r2(ffm - bcm)
    arms = [r2(0.106 * smm + rnd.gauss(0, 0.03)) for _ in range(2)]
    legs = [r2(0.30 * smm + rnd.gauss(0, 0.05)) for _ in range(2)]
    upper, lower = r2(sum(arms)), r2(sum(legs))
    trunk = r2(muscle - upper - lower)
    pa = round(6.8 + rnd.gauss(0, 0.12), 1)
    seg_score = lambda base: r2(base + rnd.gauss(0, 0.05))
    arm_s = [seg_score(-0.4), seg_score(-0.45)]
    leg_s = [seg_score(0.2), seg_score(0.15)]
    when = START + timedelta(days=day, minutes=rnd.randint(-40, 40))
    age = when.year - 1990 - ((when.month, when.day) < (3, 15))
    raw = ["0.000"] * q.N_FIELDS
    put = lambda i, v, fmt="%.3f": raw.__setitem__(i, fmt % v if not isinstance(v, str) else v)
    for i, v in ((q.ID, PERSON["id"]), (q.NAME, PERSON["name"]), (2, ""), (3, ""), (4, "0"),
                 (q.SEX, PERSON["sex"]), (q.DOB, PERSON["dob"]), (q.AGE, str(age)),
                 (q.MACHINE_TIME, (when - timedelta(hours=q.TZ_HOURS)).strftime("%Y-%m-%d %H:%M:%S"))):
        put(i, v)
    put(q.HEIGHT, PERSON["height"], "%.1f")
    F = q.FIELDS
    vals = {
        "weight_kg": w, "bmi": r2(w / h ** 2), "pbf_pct": pbf, "fat_kg": fat, "essential_fat_kg": ess,
        "storage_fat_kg": storage, "ffm_kg": ffm, "ffm_pct": r2(ffm / w * 100), "muscle_kg": muscle, "smm_kg": smm,
        "body_cell_mass_kg": bcm, "ecm_kg": ecm, "protein_kg": protein, "tbw_kg": tbw, "icw_kg": icw, "ecw_kg": ecw,
        "bmc_kg": bmc, "bmd_gcm2": r2(1.22 + rnd.gauss(0, 0.03)), "bmd_tscore": r2(0.4 + rnd.gauss(0, 0.2)),
        "vfa_cm2": r2(98 - 0.25 * day + rnd.gauss(0, 2.5)), "phase_angle": pa, "bmr_kcal": round(370 + 21.6 * ffm),
        "smi": r2(smm / h ** 2), "asmi": r2((upper + lower) / h ** 2), "upper_limb_muscle_kg": upper,
        "lower_limb_muscle_kg": lower, "weight_pct_of_std": r2(w / (22 * h ** 2) * 100),
    }
    for k, v in vals.items():
        put(F[k], v, "%.1f" if F[k] in (10, 11) else "%.3f")
    # 正常範圍（機器每次給的；這裡用簡單的比例造）
    ideal = 22 * h ** 2
    for k, (lo, hi) in {"weight_kg": (0.85 * ideal, 1.15 * ideal), "pbf_pct": (10, 20), "fat_kg": (0.10 * ideal, 0.20 * ideal),
                        "ffm_kg": (0.80 * ideal, 0.98 * ideal), "protein_kg": (0.16 * ideal, 0.20 * ideal),
                        "tbw_kg": (0.58 * ideal, 0.72 * ideal), "bmc_kg": (0.040 * ideal, 0.055 * ideal),
                        "body_cell_mass_kg": (0.52 * ideal, 0.64 * ideal), "ecm_kg": (0.24 * ideal, 0.30 * ideal)}.items():
        put(q.RANGES[k][0], r2(lo)); put(q.RANGES[k][1], r2(hi))
    # 各肢段 kg 與評分
    names = list(q.SEGMENTS)                       # 右上肢、左上肢、軀幹、右下肢、左下肢
    seg = {"右上肢": (arms[0], arm_s[0]), "左上肢": (arms[1], arm_s[1]), "軀幹": (trunk, seg_score(-0.2)),
           "右下肢": (legs[0], leg_s[0]), "左下肢": (legs[1], leg_s[1])}
    for n in names:
        p = q.SEGMENTS[n]
        put(p["muscle"][0], seg[n][0]); put(p["muscle"][1], seg[n][1])
        part = {"右上肢": 0.07, "左上肢": 0.07, "軀幹": 0.5, "右下肢": 0.18, "左下肢": 0.18}[n]
        put(p["fat"][0], r2(fat * part)); put(p["fat"][1], seg_score(0.6))
        put(p["bone"][0], r2(bmc * part)); put(p["bone"][1], seg_score(0.1))
    # 整體評分
    S = q.SCORES
    put(S["upper_limb_muscle"][0], r2(sum(arm_s) / 2)); put(S["lower_limb_muscle"][0], r2(sum(leg_s) / 2))
    for k, base in (("fat", 0.8), ("bmc", 0.1), ("muscle", -0.1), ("tbw", -0.2), ("smi", 0.3), ("asmi", 0.2)):
        put(S[k][0], seg_score(base))
    put(S["pa"][0], r2(1.4 * (pa - 6.4)))
    raw[-1] = base64.b64encode(bytes(rnd.randrange(256) for _ in range(150))).decode()     # 假的簽章
    return raw, when.strftime("%Y-%m-%d %H:%M")

def series(n, seed=7):
    rnd = random.Random(seed)
    day, out = 0.0, []
    for i in range(n):
        out.append(measurement(i, rnd, day))
        day += rnd.choice((1, 2, 2, 3, 4))
    return out

def qr_text(raw):
    return base64.b64encode(gzip.compress(",".join(raw).encode("utf-8"), mtime=0)).decode()

def write_qr_png(raw, path):
    """內容沒變就不重寫：PNG 壓縮結果依 Pillow／zlib 版本而異，每台機器的位元組不一樣。"""
    import zxingcpp
    from PIL import Image
    import starbia_qr as q
    if os.path.exists(path) and q.rows_from_image(Image.open(path)) == [raw]:
        return
    code = zxingcpp.create_barcode(qr_text(raw), zxingcpp.BarcodeFormat.QRCode)
    img = zxingcpp.write_barcode_to_image(code, scale=8)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(img).save(path, optimize=True)

def run(data_dir, *args):
    env = {**os.environ, "BODYCOMP_DIR": data_dir}
    return subprocess.run([sys.executable, *args], cwd=REPO, env=env, check=True, capture_output=True, text=True).stdout

def build(data_dir, rows):
    """寫入原始列 → 重建 CSV → 產生報表。data_dir 是相對 repo 的資料夾名。"""
    import starbia_qr as q
    root = os.path.join(REPO, data_dir)
    shutil.rmtree(root, ignore_errors=True)
    for raw, when in rows:
        day, t = when.split()
        p = os.path.join(root, "raw", day, f"qr_{t.replace(':', '')}.csv")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f: f.write(",".join(raw) + "\n")
        bad = q.consistency_issues(raw)
        assert not bad, f"{when} 欄位關係不成立：{bad}"
    run(data_dir, "scripts/starbia_qr.py", "--rebuild")
    return run(data_dir, "scripts/report.py")

def check():
    import starbia_qr as q
    from PIL import Image
    rows = series(12)
    tmp = tempfile.mkdtemp(prefix="starbia-check-", dir=REPO)
    name = os.path.basename(tmp)
    try:
        # QR 圖 → 讀回來要跟原始列一模一樣
        png = os.path.join(tmp, "qr.png")
        write_qr_png(rows[-1][0], png)
        got = q.rows_from_image(Image.open(png))
        assert got == [rows[-1][0]], "QR 圖讀回來的內容不一樣"
        # 經過正式的寫入流程（含重複檢查）
        root = os.path.join(name, "data")
        run(root, "scripts/starbia_qr.py", png, "--at", rows[-1][1], "--apply")
        out = run(root, "scripts/starbia_qr.py", png, "--at", rows[-1][1], "--apply")
        assert "SKIP" in out, "同一個 QR 重複寫入沒有被擋下"
        # 0、1、2、12 筆都要能產生報表
        os.makedirs(os.path.join(tmp, "empty"))
        print(run(os.path.join(name, "empty"), "scripts/report.py").strip())
        for n in (1, 2, len(rows)):
            out = build(os.path.join(name, f"n{n}"), rows[:n]).strip()
            files = os.listdir(os.path.join(tmp, f"n{n}", "reports"))
            assert len([f for f in files if f.endswith(".md")]) == n, f"{n} 筆量測應該有 {n} 份當日報表"
            assert os.path.exists(os.path.join(tmp, f"n{n}", "history.md"))
            print(f"{n} 筆：{out}")
        # 螢幕抄錄驗證：正確的抄錄 → ✅；抄錯一個數字 → 報表程式要回傳失敗
        raw, when = rows[-1]
        nd = os.path.join(tmp, f"n{len(rows)}")
        sv = os.path.join(nd, "raw", when[:10], "screen_values.csv")
        good = ["measured_at,page,item,shown,ref,kind,tol,note",
                f"{when},體組成分析,體重,{float(raw[q.FIELDS['weight_kg']]):.1f},v.weight_kg,num,,",
                f"{when},報告總覽,姓名,{PERSON['name']},person.name,text,,",
                f"{when},主要指標,脂肪評分長條,{float(raw[q.SCORES['fat'][0]]) + 0.03:.2f},score.fat,bar,0.05,"]
        open(sv, "w", encoding="utf-8").write("\n".join(good) + "\n")
        run(os.path.join(name, f"n{len(rows)}"), "scripts/report.py")
        stats = [l for l in open(os.path.join(os.path.dirname(sv), "VERIFY.md"), encoding="utf-8") if l.startswith("**統計")]
        assert stats == ["**統計：** ✅ 2、🟡 1\n"], f"正確的抄錄驗證結果不對：{stats}"
        open(sv, "a", encoding="utf-8").write(f"{when},體組成分析,體脂率,99.9,v.pbf_pct,num,,\n")
        try:
            run(os.path.join(name, f"n{len(rows)}"), "scripts/report.py")
            raise AssertionError("抄錯的數值沒有被抓到")
        except subprocess.CalledProcessError:
            pass
        # 把量測都刪掉 → 舊的報表也要清掉
        shutil.rmtree(os.path.join(tmp, "n2", "raw"))
        run(os.path.join(name, "n2"), "scripts/starbia_qr.py", "--rebuild")
        run(os.path.join(name, "n2"), "scripts/report.py")
        assert not os.path.exists(os.path.join(tmp, "n2", "reports")) and not os.path.exists(os.path.join(tmp, "n2", "history.md")), \
            "量測刪光後舊報表沒有清掉"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("✅ 自我測試通過")

def main():
    sys.path.insert(0, HERE)
    if sys.argv[1:] == ["--check"]:
        return check()
    rows = series(14)
    print(build("demo", rows).strip())
    write_qr_png(rows[-1][0], QR_PNG)
    print(f"→ demo/、{os.path.relpath(QR_PNG, REPO)}（最新一次量測 {rows[-1][1]} 的 QR）")

if __name__ == "__main__":
    main()
