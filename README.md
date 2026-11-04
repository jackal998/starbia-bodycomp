# STARBIA 701 體組成：QR code → 資料 → 報表

把 **STARBIA 701** 體組成分析儀「掃描報告結果」畫面上的 **QR code** 拍下來，丟進 GitHub issue，
GitHub Actions 就會解析出完整數值、寫成 CSV，並產生：

- **當日報表**：還原機器的報告版面（體組成表＋正常範圍、評估長條、骨密度、評分、各肢段、建議文字）＋與上次比較＋可信度說明
- **歷史報表**：每次量測一列、15 個指標的趨勢圖（有正常範圍的指標另有「對照正常範圍」圖）、完整數值表
- **dashboard.html**：同樣內容的單檔 HTML，圖可以互動（滑鼠移到點上看日期與數值）

全部在 GitHub 上操作，手機也可以。不需要伺服器、不需要裝任何東西。

**看範例**（虛構人物「示範」的合成資料，不是真人）：[歷史報表](demo/history.md) · [當日報表](demo/reports/2026-02-08_1901.md) · 測試用 QR：[`examples/demo_qr.png`](examples/demo_qr.png)

## 我的最新量測

<!-- latest:start -->
（還沒有量測：用「新增量測」issue 上傳第一張 QR 之後，這裡會自動顯示最新一次的摘要與趨勢圖）
<!-- latest:end -->

---

## ⚠ 先看：隱私

QR 裡有**姓名、生日、身高、性別**和所有身體數據，量測照片也會存進 repo。所以：

1. **你自己的那份一定要是 Private repo。**
2. **不要用 Fork**：public repo 的 fork 只能是 public。請用 **Use this template → Create a new repository → Private**。
3. **不要開 GitHub Pages**：repo 是 private，Pages 網站仍然是公開的。報表都是直接在 GitHub 上看的 Markdown，不需要 Pages。
4. 照片存進 repo 前會自動去掉 EXIF 的 **GPS 定位**（保留拍攝時間）。

## 開始使用（只做一次）

1. 按這個頁面上方的 **Use this template → Create a new repository**，**選 Private**。
2. 到你的新 repo：**Settings → Actions → General → Workflow permissions** 選 **Read and write permissions**
   （機器人要能留言、commit、關 issue）。
3. （選用）刪掉 `demo/` 和 `examples/`（範例資料，刪了不影響功能）。
4. 試一次：照下面「每次量完」走一遍，確認機器人有回覆。
   想先用假資料試，可以上傳 [`examples/demo_qr.png`](examples/demo_qr.png)；試完把 `body-comp/raw/` 裡那天的資料夾刪掉，
   報表會自動重建（刪光就清空）。

> 時區預設是台灣時間（UTC+8）。不同時區請改 `scripts/starbia_qr.py` 的 `TZ_HOURS, TZ_NAME`，
> 以及 `.github/ISSUE_TEMPLATE/measurement.yml` 裡寫著「台灣時間」的說明文字。

## 每次量完

1. 在機器上拍「掃描報告結果」畫面的 **QR code**。螢幕報告各頁可以順便拍（選填，會存起來供日後核對）。
   **記下實際量測時間**——機器上顯示的時間常常是錯的（見下面「機器時鐘」）。
2. 到 **Issues → New issue → 新增量測**，把照片拖進「QR code 照片」欄位。
   「實際量測時間」可以留空（= 照片拍攝時間或上傳時間），或直接填，例如 `2026-01-15 18:30`。
3. 一兩分鐘內機器人（GitHub Actions）會留言：解析出的體重、體脂率等數值，以及**它打算用的時間**和時間從哪來。
   - 時間對 → 留言 `/confirm`
   - 時間不對 → 留言 `/time 2026-01-15 18:30`，它會更新那則留言，再留言 `/confirm`
   - 也可以一步到位：`/confirm 2026-01-15 18:30`
4. 確認後它會：寫入資料 → 產生當日報表與歷史報表 → commit 到 main → 關閉 issue，留言附上報表連結。

| 留言指令 | 作用 |
|---|---|
| `/confirm` | 用目前顯示的時間寫入 |
| `/confirm 2026-01-15 18:30` | 指定時間並寫入 |
| `/time 2026-01-15 18:30` | 改時間，重新顯示（還沒寫入） |
| `/preview` | 重新解析（例如補了照片之後） |

- 一個 issue 只放**同一次**量測（同一次的 QR 拍好幾張沒關係）。
- 同一個 QR 重複送會被拒絕，不會重複寫入。
- 機器人只聽 repo 擁有者／協作者的留言。

### 出問題時

| 機器人說 | 怎麼辦 |
|---|---|
| 找不到 STARBIA 報告的 QR code | 重拍：QR 佔畫面大部分、對焦清楚、不要反光；編輯 issue 換照片，或留言補一張再 `/preview` |
| 時間看不懂 | 留言 `/time 2026-01-15 18:30`（年-月-日 時:分） |
| 時間在未來／比最新一筆還早 | 只是提醒；確定沒錯就照樣 `/confirm` |
| 下載不到圖片 | 編輯 issue 重新上傳照片；還是不行就看 Actions 的 log |
| ⚠ 處理失敗，資料沒有寫入 | 點留言裡的 log 連結看原因；資料沒寫入，修好後再 `/confirm` 即可 |
| QR 欄位關係不成立 | 機器韌體或 QR 格式可能變了，欄位對照要重新驗證（見 [驗證](docs/VALIDATION.md)） |
| 完全沒回覆 | 確認 issue 是用「新增量測」表單開的、Actions 有啟用、上面第一次設定的權限 |

## 報表怎麼看

| 檔案 | 內容 |
|---|---|
| `body-comp/reports/<日期_HHMM>.md` | 當日報表。最後的「可信度說明」講清楚哪些是確認的、哪些是推定（評分前有「≈」）、哪些沒還原 |
| `body-comp/history.md` | 歷史報表。圖依指標性質分三種畫法：**會慢慢變的**（體重、體脂、肌肉等）畫趨勢線＋「趨勢 ± 波動」帶，標近 4 週斜率 ± 標準誤與方向是否明確；**由別的指標算出來的**（BMI、基礎代謝率、SMI）只連線、不另算趨勢；**短期不該變的**（骨密度、相位角、ECW/TBW）只畫平均 ± 標準差 |
| `body-comp/dashboard.html` | 同樣內容的單檔 HTML，**圖可以互動**。GitHub 上的圖是靜態圖片，這個檔要下載後用瀏覽器開 |

**量測本身的精度：** 機器沒有公布重複性數據；同類多頻 BIA 的研究，隔天重測的體脂率誤差約 1%，
要超過約 2.8% 體脂率、1.9 kg 脂肪、2.4 kg 去脂質量才比較能確定是真的變化。
量測前固定條件（時段、空腹、排尿、量測前不運動）比連量幾次更有用。

## 正確性

- 報表用到的 QR 欄位都用機器螢幕照片逐項核對過：三天、每天 100 多項，**沒有任何不一致**。
  螢幕上沒有的 BMI、基礎代謝率用公式驗證（每次都成立）；相位角評分是推定值（報表標「≈」）。
  方法、結果與還沒還原的部分見 **[docs/VALIDATION.md](docs/VALIDATION.md)**。
- 每次上傳時機器人會檢查 12 條欄位關係（體重 = 去脂 + 脂肪、BMI = 體重 ÷ 身高² 等），不成立就警告——代表機器韌體或 QR 格式變了。
- 報表裡不確定的東西都有標（🟡 推定、⚪ 未還原）。
- **限制：** 只在一台機器、一位使用者的資料上驗證過。不同韌體可能欄位不同；女性的體脂率分類分界未經核對。
  可以用自己的螢幕照片重跑驗證（`scripts/verify_screen.py`，見 VALIDATION.md）。

## ⚠ 機器時鐘

觀察到的狀況：離線的機器開機後時鐘會回到很久以前的日期；連線時大多是 **UTC**，偶爾是本地時間。所以：

- 一律以**實際量測時間**（`measured_at`）為準：排序、去重、檔名都用它；機器時間留在 `machine_time` 欄。
- 機器時鐘正確時，機器人會用它交叉驗證實際時間（當 UTC 或本地時間換算後相差 15 分鐘內 = ✅；都對不上會提醒）。
- 機器時鐘錯的時候，機器算的年齡也可能錯 → 年齡相關的標準值可能略有偏差（報表會標出機器用的年齡）。

## 檔案

| 路徑 | 內容 |
|---|---|
| `body-comp/raw/<日期>/` | **唯一的原始資料**：`qr_<HHMM>.csv`（QR 解出的完整 222 欄）、QR 與螢幕照片 |
| `body-comp/measurements.csv` | 每次量測一列（由 raw/ 重建，勿手改） |
| `body-comp/ranges.csv` | 每次量測的正常範圍（由 raw/ 重建；`measured_at` 空白的是一般參考值，可手改） |
| `body-comp/reports/`、`history.md`、`charts/`、`dashboard.html` | 產生的報表（勿手改） |
| `scripts/starbia_qr.py` | QR 解碼、欄位對照（含可信度）、寫入與 `--rebuild` |
| `scripts/report.py` | 產生所有報表、README 最新區塊、dashboard |
| `scripts/gh_intake.py` | issue 上傳流程（由 `.github/workflows/measurement.yml` 執行） |
| `scripts/verify_screen.py` | 螢幕抄錄 vs QR 驗證 |
| `scripts/demo.py` | 產生示範資料（`demo/`、`examples/`）與自我測試 |
| `.github/workflows/reports.yml` | PR 檢查（自我測試＋產出一致）；push 到 main 時重新產生報表 |

在 GitHub 網頁上手動改了 `raw/`（例如刪掉一次量測）或腳本也沒關係：push 到 main 後 `reports.yml` 會自動重建並 commit。

### 本機手動（進階，通常用不到）

```sh
pip install -r scripts/requirements.txt
python scripts/starbia_qr.py 照片.jpg --at "2026-01-15 18:30" --apply   # 寫入一次量測
python scripts/report.py                                                 # 重新產生報表（含驗證）
python scripts/demo.py --check                                           # 自我測試
```

### QR 格式

QR 內容 = base64(gzip(一行 CSV，222 欄))，最後一欄是廠商的加密／簽章資料，讀不了。
欄位對照見 `scripts/starbia_qr.py`（`FIELDS` / `RANGES` / `SEGMENTS` / `SCORES`，每個評分欄位都標了「確認」或「推定」與依據）。
