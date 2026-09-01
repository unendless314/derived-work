# Site Build: 完整日誌留存與失敗通知

**狀態：** Open — 2026-09-01 的主要營運問題；API 模組實作前置閘門
**優先級：** P1（服務可觀測性）
**擁有者：** 雲端部署維護者
**影響範圍：** 雲端 `site_build.service`、`exopolitics-pipeline.service`，以及受 Git
追蹤的 `site_build.sh.example` 參考實作

## 1. 問題與已確認的安全性

雲端實際腳本已驗證使用 `set -e -o pipefail`。若 build 失敗：

- `site_build.sh` 會以非零狀態結束，且不會執行 `rsync`；
- `systemctl --user start site_build --wait` 會把失敗傳回 pipeline；
- pipeline 會在 Step 6 停止。

因此，這不是「失敗卻覆寫網站」或資料正確性問題。問題是失敗後無法有效判斷根因，
也沒有即時通知。

現行 `site_build.sh` 將 build 輸出接到：

```bash
npm run build 2>&1 | grep -E "(Complete|page\(s\) built|error)" | tail -5
```

這會丟棄絕大部分原始輸出。systemd journal 只留下
`Main process exited, code=exited, status=1/FAILURE` 時，維護者無法區分 Astro build
錯誤、記憶體限制、磁碟空間、依賴變動或過濾器誤判。

此外，若 build 成功但輸出不符合 `grep` 規則，`pipefail` 會將該成功 build 誤判為
失敗。`grep` 和 `tail` 應只負責摘要顯示，不能決定 build 是否成功。

## 2. 雲端調查證據（2026-09-01）

下列是雲端操作者提供的現況證據，僅記錄觀測結果，並不代表本 repository 有權直接
修改雲端腳本：

| 時間（UTC） | 元件 | 現象 | 下一次排程結果 |
|---|---|---|---|
| 2026-08-29 06:04 | `site_build.service` | exit status 1，無原始 build 輸出 | 07:00 成功 |
| 2026-08-29 12:04 | `site_build.service` | exit status 1，無原始 build 輸出 | 13:00 成功 |
| 2026-08-29 16:03 | `site_build.service` | exit status 1，無原始 build 輸出 | 17:00 成功 |
| 2026-08-31 11:05 | `site_build.service` | build 中途 exit，無原始 build 輸出 | 12:00 成功 |

同次調查確認：

- 沒有「pipeline 顯示成功但網站沒有更新」的已知事件；
- `OnFailure=`、腳本內 Telegram 通知均未設定；
- 既有每日資料庫監控通知腳本找不到，需由雲端維護者確認它是漏部署、已改名或文件過期；
- `classify` 最近 7 天沒有 HTTP 429 或 503，與本 issue 無直接關聯。

## 3. 目標

1. 每次 site build 保留足以除錯的完整原始輸出。
2. build 成敗只依 `npm run build` 的 exit code 判斷。
3. pipeline 最終失敗時，在五分鐘內向維護者送出一次通知。
4. 讓雲端維護者可在不洩露憑證、環境變數或內部設定的前提下取得失敗證據。
5. 提供參數化、無雲端硬編碼路徑的 `.example` 參考實作，但由雲端維護者決定是否及何時套用。

## 4. 架構界線：營運遙測、`analysis` 與告警

本 issue 不應把「多寫幾行 log」當成最終方案。它要補的是跨模組營運可觀測性
（operational observability）的缺口。

`analysis` 已是唯讀的營運分析 owner：它讀取已保存的紀錄，計算成功率、延遲、連續
失敗與資料新鮮度，並產出 `reports/analysis/` 報告。它不得執行 pipeline、寫入
canonical DB 或直接套用營運決策。因此，`analysis` **不**負責在錯誤發生當下收集
log、啟動 service，或自行修復問題。

本 issue 定義以下責任分工：

| 層級 | 責任 | 不得負責 |
|---|---|---|
| pipeline / systemd / site build | 產生每次 run 與 stage 的事實紀錄；保存原始 log 的位置 | 計算長期趨勢、改動 canonical 資料 |
| 營運遙測儲存與 notifier | 保存執行摘要、通知送達狀態；依明確規則傳送一次通知 | 編輯決策、資料修復、自動重跑或自動改設定 |
| `analysis` | 唯讀分析遙測與 canonical operational records，產出報告與建議 | 寫入遙測、執行 pipeline 或傳送通知 |
| `dashboard` | 顯示 analysis 的既有報告與異常訊號 | 直接讀 DB、重算指標、發送通知或改資料 |

建議資料流如下：

```text
pipeline / systemd / site build
    -> operational run and stage summaries + protected raw logs
    -> alert notifier (immediate, deduplicated)
    -> analysis (periodic, read-only trend and health reports)
    -> dashboard (read-only drill-down)
```

本階段不新建 `modules/monitoring`。現有需求仍是一個跨模組的營運能力，拆出獨立
module 會與 `analysis` 的報告責任重疊。只有在它發展出獨立 config、collector、
retention job、notifier、跨多 service integration 與自己的完整測試後，才重新評估
是否抽成 `operations` 或 `monitoring` module。

### 4.1 事件與保存規範

在實作前先制定並審閱 `docs/OPERATIONS_OBSERVABILITY.md`。它至少要定義下列
資料模型、保存位置與清理責任：

| 資料 | 最小欄位 | 建議保存位置 | 保存期限 |
|---|---|---|---|
| run 摘要 | `run_id`、開始/結束時間、結果、失敗 stage、publish generation | 獨立的 `data/operations/operations.db` | 180 天 |
| stage 摘要 | `run_id`、stage、開始/結束時間、結果、exit code、failure category、`log_path` | 同上 | 180 天 |
| 原始 log | 對應 `run_id` 的完整輸出 | 受保護的 `logs/` 目錄，不寫入 DB | 成功 30 天；失敗 90 天 |
| 通知紀錄 | rule、嚴重度、通知時間、結果、冷卻鍵 | `operations.db` | 30 天 |

`failure_category` 必須是有限且文件化的值，例如 `build_failed`、`dependency_failed`、
`out_of_memory`、`disk_full`、`service_timeout` 與 `unknown`。完整錯誤文字留在
原始 log，資料庫只保存安全、可查詢的摘要與路徑，避免將憑證或環境變數意外持久化。

### 4.2 最小告警策略

第一版只定義少數可驗證的規則：

1. pipeline 最終失敗；
2. `site_build` 失敗；
3. `current.json.last_successful_run_at` 超過既定新鮮度門檻；
4. 同一 stage 連續兩次失敗；
5. classify 的 HTTP 429 或 503 超過設定門檻。

每條規則都必須規定嚴重度、評估時間窗、通知目的地、去重鍵與冷卻時間。通知只提供
run ID、失敗 stage、時間與安全的報告/log 位置；不包含 token、API key、完整環境
變數或完整資料庫內容。修復仍由對應 operational module 的人員決定與執行。

## 5. 受 Git 追蹤的參考實作

`site_build.sh.example` 已更新為保留完整 build log，並讓摘要過濾器不影響成功與否：

```bash
LOG_DIR="${LOG_DIR:-$WORKSPACE/logs}"
BUILD_LOG="$LOG_DIR/site-build-${RUN_ID}.log"
mkdir -p "$LOG_DIR"

if ! npm run build >"$BUILD_LOG" 2>&1; then
    echo "Site build failed. Last 100 log lines:"
    tail -n 100 "$BUILD_LOG"
    exit 1
fi

grep -Ei "(complete|page\(s\) built|error)" "$BUILD_LOG" | tail -5 || true
```

此處 `|| true` 是必要的：沒有匹配的摘要行不表示 build 失敗。失敗時將最後 100 行
輸出到 journal，完整日誌仍保留在 `BUILD_LOG`。

同步評估 `pipeline.sh.example` 如何在最終失敗時發送通知。通知不得包含 API key、token、
完整環境變數或完整資料庫內容。通知通道與憑證管理方式必須先由雲端維護者確認，不在
本 issue 中預設。

## 6. 雲端套用與驗收

雲端腳本被 `.gitignore` 排除，repository 的模板更新不等於雲端變更。雲端維護者套用
前必須審閱參考實作，並以非正式或可安全隔離的方式執行以下驗收：

1. 故意使 `npm run build` 失敗，確認 `rsync` 未執行。
2. 確認完整 build log 可取得，且 journal 顯示最後 100 行。
3. 確認 pipeline 最終失敗通知在五分鐘內送達，且不含敏感資訊。
4. 執行一次正常 build，確認 deployment 正常，且摘要過濾器沒有造成誤報失敗。
5. 確認通知只對同一個 pipeline run 發送一次，避免 site build 失敗造成重複通知。
6. 重新檢查 2026-08-29 與 2026-08-31 的失敗是否可由保留的資訊歸類；若舊 log 已無法還原，明確記錄為「歷史根因不可判定」。

## 7. API 開發閘門

以下全部達成後，本 issue 才能結案，並解除 API MVP 開發前的營運閘門：

- `docs/OPERATIONS_OBSERVABILITY.md` 已鎖定 §4 的責任邊界、事件欄位、保存期與告警規則；
- 參考實作已更新並經本地 shell 語法檢查；
- 雲端維護者已決定並套用等價的日誌與通知措施；
- §5 的失敗與成功驗收均有記錄且通過；
- 已釐清每日資料庫監控通知腳本的實際狀態，或以新的通知措施明確取代它。

API 的開發本身仍須遵守 `modules/api/docs/MODULE_PROPOSAL.md` 與
`modules/api/docs/API_CONTRACT.md`，包括只讀 publish export、pointer 驗證、503
失敗模式與同批頂層文件更新。此 issue 不要求 API 在雲端對外開放。
