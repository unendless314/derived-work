# Cross-Module Issue: Curate Mother-Draft Language Drift and Translate Bypass Leakage

**Affected Modules:** `curate`, `translate`, `publish`, `site`  
**Date Reported:** 2026-09-04  
**Status:** Open — Root cause confirmed, solution planned  

---

## 1. Issue Summary & Observed Symptoms

On the production site, certain articles under the English path (`/en/posts/...`) unexpectedly display their title, summary, and structured bullets in **Simplified Chinese** instead of English.

### Concrete Examples:
- `https://exopolitics.tw/en/posts/ai-7/` (Raw Source: cnBeta `1576008`, Kioxia NAND investment)
- `https://exopolitics.tw/en/posts/fsd/` (Raw Source: cnBeta `1576000`, Tesla FSD Europe collision rates)

### Key Observations:
- **English Page (`/en/`)**: The outer layout and UI labels (e.g. `Key Claim`, `Evidence Level`, `Objective Impact`, `Disclosure`, `Source Module: curate`, `Writer Type: AI`) are in English, but the core text fields (`display_title`, `summary_short`, `bullet_1` ~ `bullet_3`) are in Simplified Chinese.
- **Traditional Chinese Page (`/zh/`)**: Correctly translated and formatted in Traditional Chinese.
- **Japanese Page (`/ja/`)**: Correctly translated and formatted in Japanese.

---

## 2. Root Cause Analysis (Root Cause Chain)

The issue is caused by a chain reaction across four distinct stages:

```text
[Chinese Source: cnBeta]
         │
         ▼
1. classify Module
   - Correctly identified primary_language_code = 'zh'
         │
         ▼ (Information Drop: curate query does not read primary_language_code)
2. curate Module
   - Prompt lacks explicit "Output MUST be English" constraint
   - In ~93% of cases, LLM translated to English naturally
   - In ~7% of cases, LLM output Chinese draft following source text inertia
         │
         ▼
3. approved_content_record Assembler
   - Hardcoded content_language_code = 'en' unconditionally
   - Blindly registered the Chinese draft as an English mother-draft
         │
         ▼
4. translate Module
   - Compares target_language ('en') == content_language_code ('en')
   - Triggers Self-Translation Bypass: directly copies Chinese draft to translation_output ('en')
   - For 'zh' and 'ja', languages differed, so LLM was invoked to translate properly
         │
         ▼
[Site Build] English pages (/en/) render Chinese content!
```

### Stage 1: Information Gap between `classify` and `curate`
- The `classify` module successfully detects and persists the source language (e.g. `primary_language_code = 'zh'`).
- However, in `modules/curate/src/database.py` (`get_pending_curation_items`), the SQL query does **not** select `c.primary_language_code`, and `curator_v1` prompt constructor never informs the LLM about the detected source language.

### Stage 2: `curate` Prompt Lacks Hard Language Constraints (Probabilistic Bug)
- In `modules/curate/config/prompt_templates.yaml` (`curator_v1`), the instructions are written in English, but there is **no explicit rule stating that outputs must always be in English**.
- When inputting Chinese text:
  - In most runs (~93%), the LLM naturally translated its summary into English.
  - In a minority of runs (~7%), the LLM kept the original language and generated `display_title`, `summary_short`, and bullets in Simplified Chinese.

### Stage 3: `approved_content_record` Blind Assumption
- In `modules/translate/src/approved_content_record.py` (lines 188–193):
  ```python
  # Under current system policy, all curate-originated mother-drafts are materialized
  # with content_language_code = 'en' (English).
  content_language_code = 'en'
  ```
  The assembler assumes all curation outputs are in English without any verification, assigning `content_language_code = 'en'` even when the text is Chinese.

### Stage 4: `translate` Self-Translation Bypass Leakage
- In `modules/translate/src/orchestrator.py` (lines 428–455):
  - When evaluating the target language `'en'`, `target_language == content_language_code` evaluates to `True` (`'en' == 'en'`).
  - The runner executes **Self-Translation Bypass** without calling any translation LLM API, directly writing the Chinese text into `translation_output` with `language_code = 'en'` and `model_name = 'bypass'`.
  - Conversely, for `zh` and `ja`, `target_language != content_language_code` triggered the LLM translation API, which translated the Chinese mother-draft into Traditional Chinese and Japanese respectively.

---

## 3. Database Empirical Findings

Analysis performed on local snapshot databases (`data/canonical_final.db` and `data/canonical.db`):

| Database | Classified as `zh` & `approved` Total | Drafted in English by Curate (No CJK) | Drafted in Chinese by Curate (Contains CJK) |
| :--- | :--- | :--- | :--- |
| `canonical_final.db` | 164 | **153 (93.3%)** | **11 (6.7%)** |
| `canonical.db` | 149 | **90 (60.4%)** | **59 (39.6%)** |

### Sample Items from `canonical_final.db`:
- **Auto-translated to English by Curate (93.3% lucky path):**
  - ID 750: *"Drone used in flood rescue in Hengzhou, Guangxi"*
  - ID 766: *"Microsoft Research Asia highlights five ACL and ICML papers..."*
  - ID 4666: *"2026 global developer frontier conference and embodied intelligence skills competition opens submissions"*
- **Remained in Chinese by Curate (6.7% leak path):**
  - ID 769: *"研究称大语言模型存在两条独立的真实性判断路径"*
  - ID 770: *"微软亚洲研究院多项文化与价值观对齐研究入选 ACL 和 ICML"*
  - ID 4334: *"美国四个微型反应堆完成临界测试，商业发电仍待后续验证"*
  - Production IDs: `ai-7` (cnBeta 1576008), `fsd` (cnBeta 1576000)

---

## 4. Recommended Solutions

### Solution 1: Enforce Output Language in `curate` Prompt (High Priority / Immediate Fix)
In `modules/curate/config/prompt_templates.yaml`, update the `curator_v1` template to add an unambiguous language requirement:

```yaml
Drafting Guidelines (when output is not null):
- Language Requirement:
  Regardless of the input source language, all output fields (curation_output.display_title,
  curation_output.summary_short, bullet_1, bullet_2, bullet_3, and editor_brief)
  MUST be strictly written in English.
```

*Pros:* Minimal code changes; directly stops the LLM from generating Chinese mother-drafts.

---

### Solution 2: Defensive Validation in `approved_content_record` (Zero-Trust Gate)
In `modules/translate/src/approved_content_record.py`:
- Add a lightweight language / script sanity check before persisting a mother-draft as `en`.
- For example, if `content_language_code == 'en'` but the text contains significant CJK characters (`[\u4e00-\u9fff]`), reject the handoff or flag it for review/re-curation instead of silently passing it through.

*Pros:* Prevents silent pollution of the downstream translation pipeline if the curate LLM violates instructions.

---

### Solution 3: Data Remediation for Existing Polluted Records
For the ~11 items in the database and current production exports:
1. Identify all `approved_content_record` rows where `content_language_code = 'en'` but `display_title` contains CJK characters.
2. Re-run `curate` with the updated English-only prompt, OR update their `approved_content_record` and trigger re-translation (`stale` status) to regenerate `en` via translation.
3. Re-export and rebuild static pages via `publish` and `site`.
