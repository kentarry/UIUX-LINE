# LINE 美術圖審查工具 v2.0

LINE Bot 自動接收設計圖，透過 AI 進行 UX/UI 審查並即時回覆建議。

## ✨ v2.0 新功能

- 🔄 **多 Key 輪替** — 支援多個 Gemini API Key 自動輪替，避免 429 配額限制
- ⏳ **指數退避重試** — 遇到 429 自動等待重試（2→4→8 秒）
- 🛡️ **OpenRouter 備援** — 所有 Gemini Key 耗盡時自動切換到 OpenRouter 免費模型
- ☁️ **雲端部署就緒** — 一鍵部署到 Render.com，24/7 不間斷運行
- 🎯 **精準知識庫** — 支援 NotebookLM 萃取的專案特定規範，限縮 AI 判斷範圍
- 📊 **AI 狀態監控** — `/health` 端點顯示所有 Key 狀態

## 🏗️ 架構

```
LINE 使用者
    ↓ 傳送圖片
LINE Platform → Webhook (HTTPS)
    ↓
Flask Server (Render.com 或本地)
    ↓ 下載圖片 + 壓縮
AI Client
    ├── Gemini Key #1 (輪替)
    ├── Gemini Key #2 (輪替)
    ├── Gemini Key #3 (輪替)
    └── OpenRouter 備援 (fallback)
    ↓ 分析結果
LINE 使用者 ← 回覆建議
```

## 🚀 快速開始

### 1. 安裝

```bash
pip install -r requirements.txt
```

### 2. 設定 .env

```bash
cp .env.example .env
# 編輯 .env 填入 API Keys
```

**多 Key 設定（推薦！避免 429）：**
```env
GOOGLE_API_KEYS=key1,key2,key3
```

### 3. 檢查環境

```bash
python cli.py check
python cli.py test-gemini   # 測試所有 Key
python cli.py test-line     # 測試 LINE 連線
```

### 4. 啟動

```bash
# 本地開發
python cli.py serve --debug

# 正式環境（使用 waitress）
python cli.py serve
```

## ☁️ 雲端部署（Render.com）

### 步驟

1. 將程式碼推到 GitHub
2. 到 [Render.com](https://render.com) 建立帳號
3. New → Web Service → 連接 GitHub repo
4. 設定環境變數：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`
   - `GOOGLE_API_KEYS`（逗號分隔多個 Key）
   - `OPENROUTER_API_KEY`（選用備援）
   - `PORT`（Render 預設 10000）
5. Deploy → 取得 HTTPS URL
6. 到 LINE Developers Console → Webhook URL 填入 `https://your-app.onrender.com/callback`

### 保持喚醒（免費方案）

Render 免費方案 15 分鐘無流量會休眠。設定 [cron-job.org](https://cron-job.org) 每 14 分鐘 ping `/health`：

```
URL: https://your-app.onrender.com/health
頻率: 每 14 分鐘
```

## 🎯 知識庫精準化

### 檔案結構

```
knowledge/
├── design_rules.md        # 基礎通用規範
├── project_specific.md    # NotebookLM 萃取的專案特定規範
├── common_issues.md       # 常見問題和修正案例
└── review_examples.md     # 好/壞範例對照
```

### 從 NotebookLM 更新知識庫

1. 在 NotebookLM 建立 Notebook，匯入品牌規範、過往審查紀錄
2. 讓 NotebookLM 生成精華摘要
3. 將摘要貼到 `knowledge/project_specific.md`
4. 執行 `python cli.py update-knowledge`

### 精準度原理

AI 的 `system_instruction` 會明確限制：
- ✅ 只根據 knowledge/ 中的規範判斷
- ✅ 每個建議必須對應具體規範條目
- ❌ 不引入外部通用知識
- ❌ 不硬找問題

## 📋 CLI 命令

| 命令 | 說明 |
|------|------|
| `check` | 檢查環境設定 |
| `serve` | 啟動 webhook 伺服器 |
| `analyze <圖片>` | 手動分析圖片 |
| `history` | 查看分析歷史 |
| `test-line` | 測試 LINE 連線 |
| `test-gemini` | 測試所有 Gemini Key + OpenRouter |
| `update-knowledge` | 更新知識庫快取 |
| `cleanup` | 清理暫存圖片 |

## 📁 檔案結構

```
├── config.py          # 設定（多 Key、備援、路徑）
├── ai_client.py       # AI 客戶端（輪替、重試、備援）
├── analyzer.py        # 分析引擎（知識庫、prompt）
├── server.py          # Webhook 伺服器
├── line_client.py     # LINE API 封裝
├── cli.py             # CLI 入口
├── skills/
│   └── ux_review.md   # UX 審查 Skill Prompt
├── knowledge/
│   ├── design_rules.md       # 基礎規範
│   ├── project_specific.md   # 專案規範
│   ├── common_issues.md      # 常見問題
│   └── review_examples.md    # 審查範例
├── Procfile           # Render 部署設定
├── render.yaml        # Render 服務定義
└── runtime.txt        # Python 版本
```
