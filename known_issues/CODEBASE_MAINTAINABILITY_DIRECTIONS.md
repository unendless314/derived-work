# 代碼庫可維護性改進方向（記錄用）

**狀態：** 方向記錄（2026-07-25），**待工程師詳盡調查與分析**；前置的翻譯標籤洩漏重構已於 2026-07-26 全部完成並結案，啟動條件已達成
**性質：** 非緊急、非阻斷性問題；此文件只記錄方向與證據位置，不構成實作計畫
**關聯文件：**
- 已結案的重構：[`TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md`](./resolved/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md)（2026-07-26 結案，原「完成前本文件所列項目不動工」的限制已解除）
- 模組邊界現況：[`../docs/MODULE_BOUNDARIES.md`](../docs/MODULE_BOUNDARIES.md)

## 1. 背景

2026-07-25 翻譯標籤重構 Phase 3 驗收期間，對 translate／publish／analysis 三個模組做了一次突變測試（mutation testing）與重複執行檢查，藉此檢驗測試有效性。測試整體被證實有效（四項突變全數被抓到），但過程中發現數個可維護性議題，整理如下供後續調查。

## 2. 發現的問題與方向

### 2.1 無 CI  pipeline，測試品質靠人肉把關

- **證據：** `AGENTS.md` 明載「There is no repo-wide build or CI pipeline yet」。
- **影響：** 本次發現的 flaky test（見 §2.2）只要有 CI 反覆執行早就會現形；PR 目前依賴人工記得跑對測試指令。
- **方向：** 建立最小 CI（跑三個 pytest 套件 + site 的 vitest/type-check/build），並納入「重複執行 N 次抓 flake」的做法。

### 2.2 tests package 撞名，跨模組測試無法同一 invocation 執行

- **證據：** `modules/publish/tests` 與 `modules/analysis/tests` 在同一 pytest invocation 下因 tests package 同名而收集失敗，目前 workaround 是分開執行（已記於 [`TRANSLATION_LABEL_LEAKAGE_HANDOFF.md`](./resolved/TRANSLATION_LABEL_LEAKAGE_HANDOFF.md) §6）。
- **影響：** 妨礙 §2.1 的 CI 單一指令化；新人容易踩到。
- **方向：** 調整 tests 目錄的 package 結構（例如加 `__init__.py` 命名空間或改 rootdir 配置 / `--import-mode=importlib`），讓全倉測試可一鍵執行（細部調查與驗證見獨立追蹤件：[`PYTEST_CROSS_MODULE_TEST_COLLECTION_COLLISION.md`](./PYTEST_CROSS_MODULE_TEST_COLLECTION_COLLISION.md)）。

### 2.3 orchestrator 單檔過大（god module 傾向）

- **證據：** `modules/publish/src/orchestrator.py` 約 880 行，包辦 slug 生成、payload 驗證、DB 交易、檔案發射、index/archive 聚合、staging/backup/rollback；`modules/translate/src/orchestrator.py` 約 714 行同型。兩檔註解中已自然分出 Phase A/B/C 段落。
- **影響：** 功能持續增加後導航與審查成本上升；本次標籤重構已讓檔案繼續長大。
- **方向：** 依既有 Phase 註解切分為子模組（如 validation／emission／promotion 分檔）。**時機建議在重構全部收斂後**，避免與進行中的 Phase 4/5 衝突。

### 2.4 測試不夠「多疑」：缺乏 flake 偵測與故障注入的確定性設計

- **證據：** `test_promotion_midway_failure_reversion` 的故障注入把 `os.replace` 全域下毒，連 rollback 自己的還原呼叫也被波及，加上 staging 檔案以無序 set 迭代，約每 6–10 次執行隨機失敗一次（2026-07-25 已修復：sorted 提升順序 + 固定注入點 + 全目錄樹快照斷言，見 commit `605b8db`）。
- **教訓：** 故障注入測試的注入範圍必須排除被測系統的自救路徑；依賴隨機順序的測試需要重複執行才能驗證穩定。
- **方向：** 把「突變測試抽查 + 套件連跑 N 次」納入驗收慣例或 CI；盤點其他含隨機性的測試（set/dict 迭代、時間戳同秒）。

### 2.5 文件流程偏重，需防止文件與程式漂移

- **證據：** 本次重構 Phase 1 需同步更新 21 份契約文件；現有「任何 scaffold/schema/狀態遷移必須同變更更新 module docs」規則（AGENTS.md）。
- **影響：** 文件量大本身是資產也是負擔，長期有漂移風險。
- **方向：** 調查哪些契約斷言可以機器化（例如 DDL 與 DATA_CONTRACT 的一致性檢查、export JSON 與 schema fixture 的自動比對），讓文件由測試背書而非只靠紀律。

## 3. 非範圍

- 不解決任何功能性 bug（目前無已知未解功能性 bug）。
- 不改變翻譯標籤重構的既定範圍與 Phase 順序；本文件所有項目皆排在其後。
- 不預設技術選型（CI 平台、目錄重構方式待調查後定案）。

## 4. 建議的調查順序（供參考，非定案）

1. §2.1 + §2.2 一併處理（CI 需要單一測試指令，兩者互相解鎖），投資報酬率最高。
2. §2.4 納入 CI 後自然落實。
3. §2.3 待重構收斂、程式碼穩定後再拆。
4. §2.5 為長期項，可在 CI 就位後逐步把契約斷言機器化。

## 5. 2026-07-29 補充檢查結果與優先方向

**性質：** 本節記錄一次以目前 active `modules/` 為範圍的可維護性檢查結果，提供後續排程參考，不構成正式重構計畫或承諾時程。

### 5.1 已確認事實

- **單一模組測試目前皆可通過：** 分別執行 active 模組的測試，共 260 項通過：
  - `ingest` 73 項、`classify` + `curate` 32 項、`publish` 27 項、`translate` 48 項、`analysis` 27 項、`dashboard` 17 項，以及 `site` Vitest 36 項。
  - 因此本次檢查未發現已知的未解功能性失敗；以下建議針對可維護性與未來變更成本，而非修復功能 bug。
- **跨模組 pytest 收集衝突仍存在，且影響不只原先列出的兩個 package：**
  - 同時收集 `modules/publish/tests`、`modules/analysis/tests`、`modules/translate/tests` 時，`publish` 與 `translate` 的測試會因 `ModuleNotFoundError: No module named 'tests.<module>'` 收集失敗。
  - 同時收集 `modules/analysis/tests` 與 `modules/dashboard/tests` 時，`dashboard` 也會以相同原因失敗。
  - 多個模組已存在 `tests/__init__.py`，所以僅新增該檔案不足以解決問題；後續方案須驗證 package 命名、pytest import mode 或 rootdir 設定能讓全倉單一 invocation 收集成功。
- **`publish` 是最集中的公開輸出一致性邊界：** `modules/publish/src/orchestrator.py` 約 884 行，包含 payload 驗證、canonical DB 補償、staging、檔案輸出、index/archive 聚合、promotion 與檔案 rollback。
- **LLM 執行路徑存在可驗證的結構性重複：** `modules/classify/src/orchestrator.py`、`modules/curate/src/orchestrator.py` 與 `modules/translate/src/orchestrator.py` 各自實作 request payload、HTTP 回應解析、批次處理、重試/等待、SQLite 寫入協調，且 `curate` 與 `translate` 各自包含近似的跨平台 `ProcessLock`。
- **`ingest` 是另一個大型但已有支援模組拆分的流程：** `modules/ingest/src/orchestrator.py` 約 746 行，集中來源排程、HTTP 抓取、解析、去重、清理、來源健康狀態與持久化；相對地，fetcher、parser、sanitizer、scheduler 與 repository 已分在獨立檔案，且測試覆蓋較完整。
- **下游觀測與呈現層目前結構相對穩定：** `analysis` 已分為 queries/services，`dashboard` 只讀 analysis report，`site` 有獨立 Vitest 契約測試。它們仍可逐步改善，但沒有證據顯示應先於上述寫入與編排流程重構。

### 5.2 建議方向順序（供排程評估）

1. **最高優先，模組級測試有效性盤點：** 先以每個模組目前的 spec、實作與測試為基礎，確認每個測試仍保護現行規則，再處理跨模組收集與 CI。這可避免把歷史測試債直接固化為自動化 gate。
2. **第一個模組重構，`publish`：** 依責任拆出 validation、reconciliation、artifact emission、promotion/rollback 與 DB compensation。保留既有交易與回滾契約，先以可獨立測試的純函式和明確 service 邊界降低風險。
3. **第二批一併研究，`translate`、`curate`、`classify`：** 針對重複的 LLM API 呼叫技術實作，評估是否需要共用 process lock、provider request/retry 與批次 runner。此項不是預先決定要抽取共用層；各模組的輸出驗證、提示詞與狀態轉換仍必須留在其模組內。
4. **第三批，`ingest`：** 將 per-source workflow 進一步拆為 fetch、parse/sanitize、persist 與 source-state update 等階段。因其是上游 canonical 寫入路徑，重構前後都應維持既有整合測試與 dedup/transaction 契約。
5. **後續漸進改善，`analysis`、`dashboard`、`site`：** `analysis` 可逐步集中跨模組 config discovery；`dashboard` 補各 view 的 render smoke test；`site` 明確化 generated content 與 build artifact 的 ownership。這些項目目前不應阻擋前述工作。

### 5.3 排程原則

- 先完成第 1 項後，才開始會移動責任邊界，或評估共享執行基礎設施的重構。
- 每一批重構均應保持模組邊界：`publish` 仍為下游輸出 owner，`translate` 不負責 editorial decision，`analysis`/`dashboard` 維持唯讀。
- 共用程式碼只抽取已有至少兩個模組使用、且契約已穩定的技術性能力，避免提早建立新的業務共享模組。

### 5.4 LLM API 執行層的決策備註

- `classify`、`curate` 與 `translate` 都呼叫 LLM API，但各自擁有模型與 prompt 設定、timeout、輸出限制、失敗後的資料狀態與重試資格。共用低階工具不得接管這些決策。
- 現況的重複實作雖有未來漂移風險，但目前各模組測試皆可通過；清楚、局部且可直接閱讀的重複程式碼，可能比過早建立抽象更容易維護。
- 僅當同一個低階問題或修正（例如 429 處理、timeout、backoff 或程序鎖）已在至少兩個模組重複出現，且可由相同技術契約處理時，才評估抽取共用工具。
- 若採共用工具，它應是 phase 外的受限技術能力，而非新的資料狀態機階段；它不得擁有 prompt、業務 config、輸出驗證、canonical DB 寫入或資料狀態遷移。

### 5.5 模組優先的測試審查與 CI 準備順序

在此代碼庫經歷多次 schema 與架構重構的背景下，測試審查應優先於系統級 CI 整合。CI 仍是目標，但不應在未確認測試有效性前，直接將所有歷史測試固化為合併門檻。

建議順序如下：

1. **建立基線清單：** 列出每個 active 模組的現行 spec、測試檔、測試目的與單獨執行結果，不先刪除任何測試。
2. **逐模組審查與瘦身：** 將測試逐一對照現行 spec、資料契約與實作，分類為保留、合併、改寫或刪除。
3. **處理跨模組測試收集與交接契約：** 在保留的測試已確認有效後，修正 pytest package 撞名，並驗證相鄰模組的 handoff contract。
4. **建立分階段 CI：** 先以已審查的測試提供自動報告，再加入 site 的 Vitest/type-check/build；確認穩定後才升格為阻擋合併的正式 gate。

測試只有在其保護的規則已移除、已被更精確的測試取代、與其他測試確定重複，或根本未被收集時，才應考慮刪除。不得只因測試看起來舊、難懂或尚未失敗就移除。
