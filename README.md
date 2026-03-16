<p align="center">
  <img src="Logo/CanvasTA.png" alt="CanvasTA Logo" width="240" />
</p>

<h1 align="center">CanvasTA</h1>

<p align="center">Multimodal LLM-based automation for Canvas assignment grading, human review, and feedback publishing</p>

<p align="center">
  <a href="#readme-en">
    <img src="https://img.shields.io/badge/English-Read%20Now-0A7EA4?style=for-the-badge" alt="English" />
  </a>
  <a href="#readme-zh">
    <img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E7%AB%8B%E5%8D%B3%E6%9F%A5%E7%9C%8B-E67E22?style=for-the-badge" alt="简体中文" />
  </a>
</p>

<p align="center"><strong>Switch language on this page without leaving GitHub homepage.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Review-Streamlit-FF4B4B?logo=streamlit&logoColor=white" alt="Review UI" />
  <img src="https://img.shields.io/badge/Canvas-API-1F6FEB" alt="Canvas API" />
  <img src="https://img.shields.io/badge/Open_Source-Friendly-2EA043" alt="Open Source" />
</p>

---

<a id="readme-en"></a>

## English

### Overview

CanvasTA is designed to shorten repetitive grading workflows for instructors:

- Automatically fetch student submission attachments
- Extract text (PDF/docx/txt) and visual content (scanned files/images)
- Grade automatically and output structured JSON
- Let instructors review, edit score/comments, and mark review status in UI
- Publish only approved results back to Canvas

> The UI supports an end-to-end flow: Grade -> Review -> Submit.

---

### Quick Start (Recommended)

#### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

#### 2. Initialize configuration

```powershell
copy .env.example .env
```

> Important: the program reads `.env` only, not `.env.example` automatically.  
> If you edit `.env.example` but do not copy/rename it to `.env`, your configuration will not take effect.

On macOS/Linux:

```bash
cp .env.example .env
```

Then fill in these minimum required values:

- `CANVAS_TOKEN`
- `COURSE_ID`
- `ASSIGNMENT_ID`
- `LLM_API_KEY`

---

### How to Get a Canvas Token

Follow this guide image to generate your Canvas token:

![Canvas Token Guide](Logo/guide.png)



#### 3. Start the UI

```powershell
python run_canvas_ta.py review
```

In the UI:

- Click `1) Fetch & Grade Assignments` in the sidebar
- Review each student result and save changes
- Click `2) Submit All Approved Results` (or submit one by one)

---

### API Configuration (Most Common Scenarios)

To reduce setup complexity, the project supports the following modes (resolved by priority):

### Mode A: OpenAI / Compatible Gateway (Recommended)

Works with OpenAI, OpenRouter, OneAPI, and most compatible gateways.

```env
LLM_PROVIDER=auto
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.openai.com/v1
```

Notes:

- Usually, `LLM_API_KEY` + `LLM_BASE_URL` is enough
- The client auto-appends the `chat/completions` path
- If your provider gives a full endpoint URL, use Mode B

### Mode B: Full Endpoint URL

```env
LLM_API_KEY=your_key
LLM_API_URL=https://your-gateway/v1/chat/completions
```

Notes:

- If `LLM_API_URL` is provided, it will be used directly
- Useful for fixed routes or non-standard proxy paths

### Mode C: Azure OpenAI

```env
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_azure_key
AZURE_OPENAI_API_VERSION=2024-06-01
```

Notes:

- Azure mode uses deployment-style URLs automatically
- `VISION_MODEL` / `GRADING_MODEL` should be deployment names in Azure mode

### Supported variable aliases

You can also use:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

These aliases are recognized automatically.

---

### Open Source Safety and Git Tips

The project already includes open-source-friendly defaults:

- `.env` is ignored to avoid leaking API keys
- `Results/` is ignored to avoid leaking student grades/comments
- `student_submissions/` is ignored to avoid uploading raw assignment files
- `测试文件/` is ignored to keep unrelated files out of the main branch

When preparing a public repository for the first time, run:

```powershell
git rm -r --cached .env Results student_submissions 测试文件
git add .gitignore .env.example
git commit -m "chore: prepare open-source safe defaults"
```

---

### Run Commands

Unified entrypoint:

```powershell
python run_canvas_ta.py grade   # 批改
python run_canvas_ta.py review  # 打开 UI（推荐）
python run_canvas_ta.py submit  # 提交已审核结果
```

Legacy entrypoints (still supported):

```powershell
python run_grading.py
python submit_results.py
streamlit run canvas_ta/review_ui.py
```

---

### Project Structure

- `canvas_ta/config.py`: configuration parsing (multi-provider support)
- `canvas_ta/llm_client.py`: model request client
- `canvas_ta/extractor.py`: text/vision extraction
- `canvas_ta/grader.py`: grading logic
- `canvas_ta/pipeline.py`: grading and submission pipeline
- `canvas_ta/review_ui.py`: Streamlit review workspace
- `Logo/`: project logo and Canvas token guide

---

### FAQ

1. API connection error during grading

- Check `LLM_API_KEY` in `.env` first
- If using a proxy, try a full `LLM_API_URL` endpoint
- For Azure, verify deployment names and API version

2. No student results shown in the UI

- Click `Fetch & Grade Assignments` in the sidebar
- Verify `COURSE_ID` and `ASSIGNMENT_ID`

3. Submission target not found when publishing

- Ensure the student has an actual submission in that assignment
- Ensure `student_name` can be matched to Canvas records

---

### License and Contribution

This project is licensed under Apache License 2.0. See `LICENSE` in the repository root.  

PRs and issues are welcome. Please review the configuration and safety sections before contributing to avoid exposing sensitive data.

---

<a id="readme-zh"></a>

## 简体中文

### 项目简介

CanvasTA 解决的是“老师批改流程太长、重复劳动太多”的问题：

- 自动拉取学生作业附件
- 自动提取文本（可读 PDF/docx/txt）与视觉内容（扫描件/图片）
- 自动评分并输出结构化 JSON
- 在 UI 中人工复核、修改分数/评语、标记审核状态
- 仅回传审核通过的结果到 Canvas

> 现在 UI 已支持一体化流程：在界面内直接执行“批改 -> 审核 -> 提交”。

---

### 快速开始（建议）

#### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

#### 2. 初始化配置

```powershell
copy .env.example .env
```

> 重要：程序默认只读取 `.env`，不会自动读取 `.env.example`。  
> 也就是说，用户如果只修改 `.env.example` 但不复制/重命名为 `.env`，配置不会生效。

如果你在 macOS/Linux：

```bash
cp .env.example .env
```

然后只需要先填这 4 项最小配置：

- `CANVAS_TOKEN`
- `COURSE_ID`
- `ASSIGNMENT_ID`
- `LLM_API_KEY`

---

### Canvas Token 获取（先看这个）

请按下图步骤获取 Canvas Token：

![Canvas Token 获取说明](Logo/guide.png)



#### 3. 启动 UI

```powershell
python run_canvas_ta.py review
```

进入 UI 后：

- 左侧栏点击 `1) 拉取并批改作业`
- 在主界面逐个学生审核并保存
- 点击 `2) 提交全部已审核结果` 或单个提交

---

### API 配置说明（兼容多数用户场景）

为了降低门槛，项目支持以下三类方式，按优先级自动处理。

#### 方式 A：OpenAI/兼容网关（推荐）

适用于 OpenAI 官方、OpenRouter、OneAPI、大多数第三方代理。

```env
LLM_PROVIDER=auto
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.openai.com/v1
```

说明：

- 只填 `LLM_API_KEY` + `LLM_BASE_URL` 即可
- 系统会自动拼接 `chat/completions` 请求路径
- 如果你的网关给的是“完整接口 URL”，请用方式 B

#### 方式 B：完整 URL 直连

```env
LLM_API_KEY=your_key
LLM_API_URL=https://your-gateway/v1/chat/completions
```

说明：

- 当 `LLM_API_URL` 存在时，直接使用它
- 适合固定路由、非标准路径代理

#### 方式 C：Azure OpenAI

```env
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_azure_key
AZURE_OPENAI_API_VERSION=2024-06-01
```

说明：

- Azure 模式会自动使用 deployment 风格 URL
- `VISION_MODEL` / `GRADING_MODEL` 在 Azure 模式下应填写 deployment 名称

#### 兼容变量别名

你也可以使用：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

项目会自动识别。

---

### 开源安全与 Git 提交建议

项目已内置开源友好规则：

- `.env` 默认忽略，不上传任何 API Key
- `Results/` 默认忽略，避免学生成绩和评语泄露
- `student_submissions/` 默认忽略，避免提交原始作业文件
- `测试文件/` 默认忽略，减少无关内容进入主分支

首次转开源仓库时，建议执行：

```powershell
git rm -r --cached .env Results student_submissions 测试文件
git add .gitignore .env.example
git commit -m "chore: prepare open-source safe defaults"
```

---

### 运行方式

统一入口：

```powershell
python run_canvas_ta.py grade   # 批改
python run_canvas_ta.py review  # 打开 UI（推荐）
python run_canvas_ta.py submit  # 提交已审核结果
```

兼容旧入口：

```powershell
python run_grading.py
python submit_results.py
streamlit run canvas_ta/review_ui.py
```

---

### 目录说明

- `canvas_ta/config.py`：配置解析（支持多 API 模式）
- `canvas_ta/llm_client.py`：模型请求层
- `canvas_ta/extractor.py`：文本/视觉提取
- `canvas_ta/grader.py`：评分逻辑
- `canvas_ta/pipeline.py`：批改与提交流程
- `canvas_ta/review_ui.py`：Streamlit 审核工作台
- `Logo/`：项目 Logo 与 Canvas API 获取说明图

---

### 常见问题

1. 批改时报 API 连接错误

- 优先检查 `.env` 的 `LLM_API_KEY`
- 如果是代理，先改为 `LLM_API_URL` 完整地址验证
- 若是 Azure，确认 deployment 名称与 API version

2. UI 没有学生结果

- 在 UI 左侧点击 `拉取并批改作业`
- 检查 `COURSE_ID` / `ASSIGNMENT_ID` 是否正确

3. 提交时提示找不到提交对象

- 确认学生在该作业下有真实提交记录
- 确认 `student_name` 与 Canvas 记录可匹配

---

### 许可证与贡献

本项目采用 Apache License 2.0 许可证，详见仓库根目录的 `LICENSE` 文件。  

欢迎 PR 与 Issue。建议贡献前先阅读本 README 的配置和安全部分，避免提交敏感信息。
