# fusion-reviewer

`fusion-reviewer` 是一个“委员会式 AI 审稿系统”。它不是只让一个模型看一遍论文，而是把同一篇论文交给：

- 3 个 generalist reviewer
- 5 个 specialist reviewer
- 1 个 editor / meta-reviewer（仅 backend 模式）

系统目前支持两条运行路径：

- `backend` 模式：本地服务自己完成整套委员会审稿。
- `codex` 模式：先准备共享证据包，再交给 Codex skill 拉起 8 个 subagent，主对话负责 editor 收口。

项目支持 `PDF / DOCX / DOC` 输入。默认策略是：

- PDF：优先用 `MinerU` 做高保真预处理，失败后回退到本地结构化解析，再不行才走纯文本兜底。
- DOCX：默认先 `Word -> PDF`，再走和 PDF 一样的预处理链路。
- DOC：必须先通过 LibreOffice 转 PDF，否则直接报错，不做不可靠的“假解析”。

所有运行产物都会写到：

`review_outputs/<paper_stem>/<run_id>/`

## 你最先要知道的 4 件事

1. 第一次试跑时，不需要先填真实 API key，默认 `mock_local` 就能跑通流程。
2. 真正接入外部模型时，主要改的是 [`.env`](D:/auto reviewer system/fusion-reviewer/.env)、[providers.yaml](D:/auto reviewer system/fusion-reviewer/providers.yaml)、[review_plan.yaml](D:/auto reviewer system/fusion-reviewer/review_plan.yaml)。
3. 如果你要审 `Word` 论文，建议先确保本机有 LibreOffice，这样系统会自动先转 PDF，再交给 MinerU / PDF 预处理。
4. 如果你要按某个期刊标准审稿，可以在提交任务时附带“期刊风格 / 栏目要求 / 审稿标准”文本或文件。

## 环境准备

本项目默认使用 conda 环境：

```powershell
conda run -n review-fusion-py313 fusion-review --help
```

如果上面这条能正常输出帮助，就说明环境已经可用。

## 最简单的试跑方式

先用 mock provider 跑一篇论文：

```powershell
conda run -n review-fusion-py313 fusion-review submit --paper .\sample_paper.pdf --mode backend --wait-seconds 60
```

如果你只想先准备一份给 Codex skill 使用的共享证据包：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\prepare_paper.py --paper .\sample_paper.pdf
```

## 命令行用法

### 1. 提交论文

```powershell
conda run -n review-fusion-py313 fusion-review submit --paper <论文路径>
```

常用参数：

- `--mode backend|codex`
- `--provider-profile <profile>`
- `--journal-text "这里直接粘贴期刊要求"`
- `--journal-file <期刊要求文件路径>`
- `--wait-seconds 60`

说明：

- `--paper` 是新的统一入口。
- `--pdf` 仍然保留为兼容别名，但推荐以后都用 `--paper`。
- `--journal-text` 和 `--journal-file` 是可选输入，用来告诉系统“这篇稿子准备投什么期刊、期刊要求是什么”。

### 2. 查看任务状态

```powershell
conda run -n review-fusion-py313 fusion-review status --job-id <job_id>
```

### 3. 查看最终结果

```powershell
conda run -n review-fusion-py313 fusion-review result --job-id <job_id>
```

### 4. 启动本地 Web 界面

```powershell
conda run -n review-fusion-py313 fusion-review serve
```

启动后打开浏览器访问：

`http://127.0.0.1:8123`

## Web / CLI / Codex skill 三种入口的区别

### backend 模式

适合你想“一键跑完整套委员会审稿”的情况。

它会完成：

- 文档预处理
- 3 个 generalist reviewer
- 5 个 specialist reviewer
- 1 个 editor / meta-review
- concerns 合并
- final report 导出

### codex 模式

适合你想让 Codex 自己扮演 editor，并行拉 8 个 subagent 的情况。

推荐工作流是：

1. 先用 `prepare_paper.py` 准备共享证据包
2. 再让 Codex skill 读取该 run directory
3. Codex 拉起 8 个 subagent
4. 主对话作为 editor 做最终收口
5. 当 `reviews/` 已写满 reviewer JSON 后，直接运行正式收口命令：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-reviews --run-dir <run_dir>
```

如果当前环境还没有 `fusion-review` 命令，再退回到：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\finalize_run.py --run-dir <run_dir>
```

这两条命令都会自动从 run 目录恢复 title/source-name，不需要再手写一次性临时脚本去拼 reviewer 结果。

如果 reviewer / editor 结果当前只存在于两个临时 UTF-8 JSON 文件里，也有正式入口可用：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-payloads --run-dir <run_dir> --reviews-file .\tmp_reviews.json --editor-file .\tmp_editor.json
```

它会把 payload 正式写回 run 目录并直接完成收口，目的是替代 `tmp_write_reviews_finalize.py` 这类一次性写盘脚本。

补充说明：

- `codex` 模式下的 reviewer subagent 模型与思考强度，不走 `review_plan.yaml`，而是由 Codex skill / spawn 参数决定。
- 当前 skill 已约定默认使用 `gpt-5.4` + `xhigh` 作为 committee reviewer 的默认配置；如果你想改成别的档位，应优先改 skill 规则而不是 backend 的 provider 配置。

skill 安装目录是：

`C:\Users\12130\.codex\skills\paper-review-committee`

## 期刊要求怎么用

如果你希望系统按某个期刊的口味来审稿，可以这样做：

### 方式 A：直接粘贴文本

```powershell
conda run -n review-fusion-py313 fusion-review submit --paper .\paper.pdf --journal-text "这里是期刊栏目定位、篇幅要求、创新性标准、审稿偏好"
```

### 方式 B：传入文件

```powershell
conda run -n review-fusion-py313 fusion-review submit --paper .\paper.pdf --journal-file .\journal_requirements.txt
```

系统会把这份内容统一写到：

`evidence/journal_requirements.md`

然后让：

- 3 个 generalist reviewer
- 5 个 specialist reviewer
- editor

都同时参考这份期刊要求。

最终报告里也会区分：

- 论文本身的问题
- 与目标期刊不匹配的问题

## PDF / Word 预处理怎么做

### PDF

固定顺序是：

1. `MinerU`
2. 本地 `fitz` 结构化 fallback
3. `pypdf` 纯文本兜底

如果 MinerU 成功，系统会优先使用它的：

- markdown
- content_list
- page index
- 更完整的版面信息

### DOCX

默认先尝试：

`DOCX -> LibreOffice -> PDF -> MinerU / PDF 预处理`

只有当 LibreOffice 不可用或转换失败时，才退到较低保真的 `docx-text-fallback`。

### DOC

`.doc` 不走文本兜底，必须依赖 LibreOffice 转 PDF。这样做是为了避免老式 Word 格式在公式、图表、版式上严重失真。

## 为什么 Word 要先转 PDF 再审

因为这个系统不是普通摘要器，而是要做“带证据定位的审稿”。  
如果直接读 Word 文本，公式、表格、图、页码、段落布局都更容易丢。先转 PDF 再做 PDF 预处理，能更好保住：

- 公式和图表
- 页码与位置
- 多 reviewer 共享的同一份 evidence bundle
- 最后 PDF 审稿报告的对齐能力

## 产物目录长什么样

每次运行都会生成一个新目录：

```text
review_outputs/
  <paper_stem>/
    <run_id>/
      evidence/
        source_copy.*
        normalized.md
        plain_text.txt
        page_index.json
        structured.json
        diagnostics.json
        journal_requirements.md
      reviews/
        committee_review_<slot>.md
        committee_review_<slot>.json
        specialist_review_<category>.md
        specialist_review_<category>.json
      meta_review.md
      meta_review.json
      concerns_table.csv
      concerns_table.json
      final_report.md
      final_summary.json
      final_report.pdf
```

你最常看的通常是：

- `final_report.md`
- `final_report.pdf`
- `meta_review.md`
- `concerns_table.csv`
- `evidence/diagnostics.json`

## 需要改哪些配置文件

### [`.env`](D:/auto reviewer system/fusion-reviewer/.env)

放本机环境变量和密钥，比如：

- `MINERU_API_TOKEN`
- `OPENAI_API_KEY`
- `AIHUBMIX_API_KEY`
- `SILICONFLOW_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `LIBREOFFICE_BIN`

### [providers.yaml](D:/auto reviewer system/fusion-reviewer/providers.yaml)

定义“有哪些 provider profile 可用”，比如：

- `mock_local`
- `openai_default`
- `aihubmix`
- `siliconflow`
- `anthropic_native`
- `google_native`

### [review_plan.yaml](D:/auto reviewer system/fusion-reviewer/review_plan.yaml)

定义每个 reviewer / editor 槽位默认用哪个 profile 和 model。

### [roles.yaml](D:/auto reviewer system/fusion-reviewer/roles.yaml)

定义 3 个 generalist、5 个 specialist、1 个 editor 的角色定位、语气和 focus areas。

## API key 怎么配

先复制：

```powershell
Copy-Item .env.example .env
```

然后只填你实际要用的那一组。

例如：

- `OpenAI`：填 `OPENAI_API_KEY`
- `AIHubMix`：填 `AIHUBMIX_API_KEY` 和 `AIHUBMIX_BASE_URL`
- `硅基流动`：填 `SILICONFLOW_API_KEY` 和 `SILICONFLOW_BASE_URL`
- `Anthropic`：填 `ANTHROPIC_API_KEY`
- `Google Gemini`：填 `GOOGLE_API_KEY`
- `MinerU`：填 `MINERU_API_TOKEN`

配完后检查：

```powershell
conda run -n review-fusion-py313 fusion-review providers test
```

## LibreOffice 说明

如果你会审 `DOCX / DOC`，建议安装 LibreOffice。  
安装完成后，如果系统没有自动找到 `soffice.exe`，可以在 `.env` 里显式写：

```env
LIBREOFFICE_BIN=C:\Program Files\LibreOffice\program\soffice.exe
```

## 常见理解误区

### 1. “我填几个 API key，就有几个模型一起审吗？”

不是。  
真正决定“谁来审”的是 [review_plan.yaml](D:/auto reviewer system/fusion-reviewer/review_plan.yaml)，不是 API key 数量。

### 2. “3 个 generalist 和 5 个 specialist 会不会重复？”

会有部分重复，但这是有意保留的。  
generalist 负责全局判断，specialist 负责某一维度深挖；重复可以帮助识别真正的 committee 共识问题。

### 3. “Word 为什么不直接给 AI？”

因为你现在要的是“能引用证据、尽量不崩公式图表”的审稿系统，不是普通聊天总结。先转 PDF 再预处理，是为了尽量保留审稿需要的结构信息。

## 开发与回归测试

运行测试：

```powershell
conda run -n review-fusion-py313 python -m pytest
```

如果你修改了 skill 源码，记得重新同步安装：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\install_skill.py
```
