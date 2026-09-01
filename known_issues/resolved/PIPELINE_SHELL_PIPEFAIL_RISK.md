# Resolved: Pipeline Shell Scripts: `set -e` 缺少 `pipefail`，關鍵步驟失敗會被靜默吞掉

**狀態：** 已結案，2026-09-01
**記錄日期：** 2026-08-02
**相關文件：** `known_issues/SITE_TEST_MAINTAINABILITY_PLAN.md`（Phase 1 驗收要求部署順序與失敗中止；本檔涵蓋失敗傳遞的那一半）

## 結案驗證（2026-09-01）

本 issue 原先記錄的風險是：shell pipeline 的關鍵命令使用 pipe 時，若未設
`pipefail`，前段命令失敗可能被末端 `grep` 或 `tail` 的成功狀態遮蔽。以下驗證
已由雲端環境的操作者完成：

| 驗證項目 | 結果 |
|---|---|
| 受 Git 追蹤的參考模板 | `pipeline.sh.example` 與 `site_build.sh.example` 都使用 `set -e -o pipefail`。 |
| 雲端實際 pipeline | `/root/.openclaw/workspace/exopolitics/pipeline.sh` 第 6 行使用 `set -e -o pipefail`。 |
| 雲端實際 site build | `/root/.openclaw/workspace/exopolitics/site_build.sh` 第 6 行使用 `set -e -o pipefail`。 |
| site build 失敗傳遞 | 在三種 shell pipe 情境下驗證：`npm run build` 非零時，`site_build.sh` 以非零結束，且不執行 `rsync`。 |
| systemd 傳遞 | `systemctl --user start site_build --wait` 會把 `site_build.service` 的失敗傳回 `pipeline.sh`；配合 `set -e`，pipeline 在 Step 6 停止。 |

因此，本 issue 的失敗傳遞與避免失敗後部署需求均已滿足。

### 範圍界線

雲端實際 `.sh` 腳本依
[`OPS_SCRIPTS_GITIGNORE_AND_TEMPLATE_PLAN.md`](./OPS_SCRIPTS_GITIGNORE_AND_TEMPLATE_PLAN.md)
不受 Git 追蹤。此次結案記錄操作者已驗證的行為，並未從本 repository 修改或取代
雲端腳本。

完整 build 輸出留存與失敗通知仍未完成，已另立
[`SITE_BUILD_OBSERVABILITY_AND_FAILURE_ALERTING.md`](../SITE_BUILD_OBSERVABILITY_AND_FAILURE_ALERTING.md)
追蹤；它們不改變本 issue 的結論。

---

## 原始問題記錄

> 下列內容保留為歷史背景。當時「尚未正式部署」及「缺少 `pipefail`」的描述已由
> 上述 2026-09-01 驗證取代。

## 問題

`pipeline.sh` 與 `site-build.sh` 都以 `set -e` 宣告 fail-fast，但關鍵指令接了 pipe，且未設定 `set -o pipefail`：

- `pipeline.sh:63`：publish 指令 `python3 -m modules.publish.src.cli run ... | tail -10`
- `site-build.sh:16`：`npm run build 2>&1 | grep -E "(Complete|page\(s\) built|error)" | tail -5`

bash 的 `set -e` 只檢查 pipeline **最後一個指令**的 exit code。`tail`／`grep` 幾乎總是回傳 0（grep pattern 包含 `error`，build 失敗的輸出反而更容易匹配成功），因此：

1. publish 當掉 → `tail` 回傳成功 → pipeline 繼續觸發 Step 6 site build（違反腳本自身的 fail-fast 意圖）。
2. site build 失敗 → `grep` 回傳成功 → `rsync -a --delete` 照樣把**舊的 dist** 部署到 `/var/www/exopolitics`，log 仍寫 "Site build complete"。

## 為什麼重要

site 測試重構落地後，publish export 缺失或損壞會讓 `npm run build` hard-fail。但若部署仍走這兩支腳本，hard-fail 會被上述 pipe 遮蔽：build 失敗 → 舊站照常部署 → 無人收到警訊，等於把「壞得大聲」的設計完全抵消。順序（publish → site build）已確認正確，缺的是失敗傳遞。

## 修正方向

啟用雲端部署前，同批處理：

1. 兩支腳本的 `set -e` 旁加 `set -o pipefail`（保守起見不加 `set -u`，避免既有變數用法踩雷）。
2. 修正後以一次故意的 build 失敗驗證：`site-build.service` 必須回傳非零，且 `rsync` 不得執行。
3. 確認 `systemctl --user start site-build --wait` 會把 service 失敗傳回 `pipeline.sh`，讓 pipeline log 明確標記本次 run 失敗。

替代做法：關鍵指令不接 pipe（先重導向到暫存 log 再 `tail` 顯示），效果相同，依維護者偏好擇一。
