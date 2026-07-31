---
name: daily-report
description: 整理與 Claude 的對話記錄，產出適合向上回報的工作清單。支援時間範圍選擇（今天、昨天、本週等）與自訂輸出指示。
---

# Daily Report v2 — 每日工作回報整理

## 使用方式

```
/daily-report                      # 預設：今天
/daily-report 昨天                  # 昨天的對話
/daily-report 本週                  # 本週（週一至今）
/daily-report 上週                  # 上週（週一至週日）
/daily-report 本月                  # 本月 1 日至今
/daily-report 用英文輸出            # 今天，改用英文
/daily-report 本週 只列重要工作     # 本週 + 額外指示
```

---

## 執行步驟

### Step 0：解析使用者傳入的 args

從 `/daily-report` 後面的文字中，依下列規則擷取兩個變數：

**時間關鍵字 → 日期範圍**

| 關鍵字 | start_date | end_date |
|---|---|---|
| （無 / 今天 / today） | 今天 | 今天 |
| 昨天 / yesterday | 昨天 | 昨天 |
| 本週 / this week | 本週一 | 今天 |
| 上週 / last week | 上週一 | 上週日 |
| 本月 / this month | 本月 1 日 | 今天 |

**額外指示（additional_prompt）**

去除時間關鍵字後，剩餘的文字視為 additional_prompt，例如：
- `/daily-report 本週 只列重要工作` → additional_prompt = `只列重要工作`
- `/daily-report 用英文輸出` → additional_prompt = `用英文輸出`（時間預設今天）

---

### Step 1：讀取指定範圍的對話紀錄（history.jsonl）

將 start_date / end_date 代入下方 Python 腳本執行：

```bash
python3 -c "
import json, datetime, os

start = datetime.date.fromisoformat('START_DATE')
end   = datetime.date.fromisoformat('END_DATE')
entries = []

history_path = os.path.expanduser('~/.claude/history.jsonl')
if not os.path.exists(history_path):
    exit()

with open(history_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            ts = data.get('timestamp', 0)
            if not ts:
                continue
            dt = datetime.datetime.fromtimestamp(ts / 1000).date()
            if start <= dt <= end:
                display = data.get('display', '').strip()
                project = data.get('project', '')
                time_str = datetime.datetime.fromtimestamp(ts / 1000).strftime('%m/%d %H:%M')
                entries.append({
                    'display': display,
                    'project': project.replace('/Users/vokeyu', '~').replace('/Users/$(whoami)', '~'),
                    'time': time_str,
                    'date': dt.isoformat()
                })
        except:
            pass

for e in entries:
    if e['display']:
        print(f\"[{e['time']}] {e['project']} | {e['display']}\")
"
```

---

### Step 2：讀取專案對話細節（projects/*.jsonl）

```bash
python3 -c "
import json, datetime, os, glob

start = datetime.date.fromisoformat('START_DATE')
end   = datetime.date.fromisoformat('END_DATE')
projects_dir = os.path.expanduser('~/.claude/projects')
messages = []

for jsonl_file in glob.glob(f'{projects_dir}/*/*.jsonl'):
    project_slug = jsonl_file.split('/')[-2]
    project_name = project_slug.replace('-Users-vokeyu-Desktop-code-', '').replace('-Users-vokeyu-', '~/')
    try:
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get('type') != 'user':
                    continue
                ts = data.get('timestamp', '')
                if not ts:
                    continue
                dt = datetime.date.fromisoformat(ts[:10])
                if not (start <= dt <= end):
                    continue
                msg = data.get('message', {})
                content = msg.get('content', '')
                text = ''
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get('type') == 'text':
                            text = c.get('text', '').strip()
                            break
                elif isinstance(content, str):
                    text = content.strip()
                time_str = ts[5:16].replace('T', ' ')
                if text and len(text) > 3:
                    messages.append(f'[{time_str}] {project_name} | {text[:200]}')
    except:
        pass

for m in messages:
    print(m)
"
```

---

### Step 3：整合並產出回報清單

將 Step 1 與 Step 2 的資料合併，進行以下處理：

1. **去重**：同一訊息若在兩來源都出現，只保留一筆
2. **跨日分組**：若範圍超過一天，在專案分組內可加日期標註（如「5/20」）
3. **依專案分組**：把同一個 repo / 專案的工作歸在一起
4. **改寫語言**：把工程師的指令或技術用語，轉成業務行為描述

#### 改寫原則

| 原始 prompt 類型 | 轉換方向 |
|---|---|
| 修 bug、fix error | 修正 XX 功能的顯示/操作異常 |
| 新增 feature | 開發 / 完成 XX 功能 |
| refactor、重構 | 優化 XX 模組的程式結構 |
| 部署、CI/CD | 更新測試 / 正式環境部署流程 |
| 測試相關 | 驗證 XX 功能的正確性 |
| 文件、README | 更新技術說明文件 |
| 設計討論 | 討論並規劃 XX 功能架構 |
| 資料庫操作 | 調整資料結構以支援新需求 |

#### 套用 additional_prompt

若使用者有傳入額外指示，在生成輸出前將其作為補充規則套用，例如：
- `只列重要工作` → 省略瑣碎或重複性操作，只保留有明確產出的工作項目
- `用英文輸出` → 所有輸出改用英文
- `加上預估工時` → 每個 bullet 後面加上預估花費時間

#### 輸出格式

單日：
```
📋 工作摘要 — YYYY/MM/DD

1. [專案/系統名稱]
   • 完成 XX 功能開發並通過測試
   • 修正 YY 頁面的操作流程問題

2. [另一個專案]
   • 優化 AA 模組的效能

共協作 N 個專案，處理 M 項工作。
```

多日（本週／上週／本月）：
```
📋 工作摘要 — YYYY/MM/DD ～ YYYY/MM/DD

1. [專案/系統名稱]
   • [5/20] 完成 XX 功能開發
   • [5/22] 修正 YY 頁面問題

2. [另一個專案]
   • [5/21] 優化 AA 模組的效能

共協作 N 個專案，橫跨 D 天，處理 M 項工作。
```

---

## 注意事項

- 若指定範圍內沒有任何對話記錄，回覆「指定期間內尚無與 Claude 的對話記錄」
- 若某條 prompt 太模糊（如單字回覆、看一下、好），略過不列入
- 每個項目一律使用 `•` 符號（不可用 `-`）
- 每個 bullet 字數控制在 20–50 字之間，清楚說明做了什麼
- 不要列出實作細節（函式名稱、指令、檔案路徑）
- additional_prompt 若與格式衝突，以使用者指示為優先

---

## 範例

```
/daily-report 本週
```

```
📋 工作摘要 — 2026/05/20 ～ 2026/05/26

1. 服務任務管理系統（service-tasks）
   • [5/20] 調整任務操作區的提示訊息位置，改善使用者體驗
   • [5/22] 修正 checkbox 停用狀態的 tooltip 顯示邏輯

2. 薪資系統（Salary）
   • [5/21] 討論並確認薪資計算模組的設計方向

3. 工具與環境設定
   • [5/26] 建立 daily-report v2 skill，支援時間範圍與自訂輸出

共協作 3 個專案，橫跨 5 天，處理 4 項工作。
```
