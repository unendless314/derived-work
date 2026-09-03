# Pytest 跨模組測試收集命名衝突與 Import Mode 規範

**狀態：** Open（已記錄 Known Issue，待排程處理，非阻斷性）  
**日期：** 2026-09-04  
**優先級：** P3 / P4（工程體驗與可維護性）  
**影響範圍：** 本地跨模組全跑測試指令、未來 CI Pipeline 整合  
**關聯模組：** 全模組、`modules/classify/tests/`  
**關聯文件：**
- [`CODEBASE_MAINTAINABILITY_DIRECTIONS.md`](./CODEBASE_MAINTAINABILITY_DIRECTIONS.md) §2.2 與 §5.1

---

## 1. 背景與通報來源

2026-09-03 在完成 `modules/api` 模組開發驗收時，工程師在倉庫根目錄執行 `pytest modules/` 嘗試進行全倉測試，回報遭遇 56 個測試收集錯誤（Collection Errors），錯誤訊息均為 `ModuleNotFoundError: No module named 'tests.test_<name>'`。

經調查與複驗確認：
1. **非本次改動引入**：此問題為代碼庫長期存在的既有測試收集行為，即便不包含 `api` 模組，同樣會產生 50+ 個 collection error。
2. **所有測試邏輯 100% 正確**：各模組單獨執行測試皆為全綠；在適當的 import mode 下，全倉 851 個測試全數通過（850 passed, 1 skipped, 0 failed）。

---

## 2. 根本原因分析 (Root Cause Analysis)

### 2.1 tests/__init__.py 分佈不一致
目前倉庫內 8 個模組中，有 7 個模組的測試目錄含有 `__init__.py`：
- `modules/analysis/tests/__init__.py`
- `modules/api/tests/__init__.py`
- `modules/curate/tests/__init__.py`
- `modules/dashboard/tests/__init__.py`
- `modules/ingest/tests/__init__.py`
- `modules/publish/tests/__init__.py`
- `modules/translate/tests/__init__.py`

唯獨 **`modules/classify/tests/` 缺少 `__init__.py`**。

### 2.2 Pytest 預設 prepend 模式下的 Package 命名空間碰撞
Pytest 預設的模組匯入機制為 `--import-mode=prepend`。當 pytest 遞迴掃描 `modules/` 下的多個測試目錄時：
- 每個模組的測試子目錄都命名為 `tests`。
- 當 pytest 進入第一個含有 `__init__.py` 的模組時，會將其作為頂層套件 `tests` 註冊至 `sys.modules`。
- 當掃描到缺少 `__init__.py` 的 `classify/tests`，或是接續掃描其他模組的 `tests/` 時，Python 模組解析器試圖從已註冊的 `tests` 套件尋找其他模組專屬的測試檔（例如 `tests.test_database`），因而引發大量的 `ModuleNotFoundError`。

---

## 3. 驗證與現行操作指南 (Workarounds)

### 3.1 推薦：全倉一鍵測試（使用 importlib 模式）
若需要從專案根目錄一次執行所有模組測試，加上 `--import-mode=importlib` 參數即可完全避開命名空間衝突：

```bash
# 收集檢查
py -3 -m pytest modules --import-mode=importlib --collect-only -q

# 全倉執行
py -3 -m pytest modules --import-mode=importlib -q
```

*實測結果（2026-09-04 驗證）：*
```text
850 passed, 1 skipped, 2 warnings, 867 subtests passed in 44.19s
```
全倉 8 個模組無任何 collection error，功能全數通過。

### 3.2 規範遵循：單模組獨立執行
依據 `AGENTS.md` 規範：「When executable code is added, prefer module-local commands from `modules/<module>/`」：
```bash
# 各模組分開跑，完全不受影響
py -3 -m pytest modules/api/tests -q
py -3 -m pytest modules/classify/tests -q
py -3 -m pytest modules/curate/tests -q
# ...其餘模組依此類推
```

---

## 4. 建議的永久修復方案 (Proposed Permanent Solutions)

建議在未來排程或建立 CI 時採用以下改善步驟（不需緊急抽調人力處理）：

### 方案 A：根目錄配置 pytest.ini / pyproject.toml（最推薦，零副作用）
在專案根目錄建立 `pytest.ini`，將 `importlib` 設為預設模式：
```ini
[pytest]
addopts = --import-mode=importlib
testpaths = modules
```
*優點*：
- 徹底解決 pytest 預設 prepend 模式的同名 package 衝突。
- 這是 pytest 官方現代最佳實踐（Best Practice）。
- 完全不需要修改任何模組的程式碼或測試結構。

### 方案 B：補齊 `modules/classify/tests/__init__.py`
為 `modules/classify/tests/` 建立一個空的 `__init__.py`，使各模組測試目錄結構一致。  
*注意*：僅補 `__init__.py` 可能無法完全杜絕 prepend 模式下 `tests` 撞名的邊界情況，建議方案 A 與方案 B 搭配實施。

---

## 5. 驗收條件

1. 於專案根目錄直接執行 `pytest` 或 `pytest modules` 時，不需手動加參數即可正常收集並通過全倉測試。
2. 不影響各模組獨立執行 `pytest modules/<module>/tests` 的既有行為與測試計數。
3. 若本案透過方案 A/B 解決，本文件移至 `known_issues/resolved/` 結案。
