---
name: paper-review-committee
description: Committee-style paper review for PDF, DOCX, and DOC papers with 8 subagents, shared evidence normalization, editor synthesis, and artifact bundles. Use when Codex needs to review a paper, launch multiple reviewer subagents, act as the editor for final synthesis, or produce review outputs from a local workspace.
---

# Paper Review Committee

## 概览

这个 skill 用来把“论文审稿”做成委员会模式，而不是单轮单视角输出。

标准流程是：

1. 先把论文归一化成共享 evidence bundle。
2. 先做研究范式分类，写出 `evidence/manuscript_classification.json`。
3. 并行启动 8 个 reviewer subagent：3 个 generalist + 5 个 specialist。
4. 由主对话承担 editor，负责去重、辨别共识与分歧、收口和产物写入。

当需要 Codex 自己完整跑完委员会时，用 `codex` 模式。  
当需要本地 `fusion-reviewer` service 和 provider profiles 跑同一套角色与 schema 时，用 `backend` 模式。

## 包含内容

这个 skill 包应至少包含这些文件：

- `SKILL.md`：主流程、边界、命令和审稿规则
- `agents/openai.yaml`：Codex UI/插件侧展示与默认提示
- `references/roles.yaml`：8 位 reviewer、editor、分类门控和证据规则
- `references/artifact_contract.md`：输出目录、JSON 字段和合并规则
- `references/paradigm_criteria.yaml`：研究范式分类标签和范式对应的方法审查标准
- `scripts/prepare_paper.py`：生成共享 evidence bundle
- `scripts/finalize_run.py`：从 reviewer/editor JSON 收口成最终产物
- `scripts/install_skill.py`：把 skill 安装到当前用户的 Codex skills 目录

打包或分享时不要包含 `__pycache__/`、`.pyc`、临时输出目录或个人运行结果。

## 返修稿件处理

当目录或文件名中包含以下标记之一时，本次运行是返修审稿：
`返修`、`修订`、`revised`、`revision`、`response`、`修改说明`、`答复`

返修稿件必须执行以下检查流程：

1. **检查返修说明是否存在**：查看 `evidence/revision_notes.md` 是否存在。
2. **如果不存在**：主动询问用户提供返修说明/答复信（revision text 或 revision file）。不要假装返修回应已审。
3. **如果存在但内容乱码或过短**：在最终报告中明确标注"返修说明不可读/不完整，返修回应审稿未完成"。
4. **如果返修说明正常**：委员会正常审稿后，系统在收口阶段自动运行返修回应审稿，比较原始关注点与作者回应。
5. **返修回应审稿结果**会写入 `revision_response_review.md` / `.json`，友好别名为 `06-返修回应审稿.md`。
6. **codex 模式注意**：`finalize_codex_run()` 需要 `provider_profile` 参数才能运行返修回应审稿。如果 codex 环境没有 API key，返修回应审稿会被跳过并在报告中说明限制。

## 适用场景

- 输入是 `PDF`、`DOCX` 或 `DOC` 论文
- 用户希望得到 committee-style peer review
- 用户希望看到多个独立 reviewer 视角
- 用户希望最后落下 artifacts，而不只是聊天摘要
- 用户希望附带“期刊风格 / 审稿标准 / 栏目要求”一起审

## 工作流

### 1. 先做共享预处理

始终先归一化源文件：

- PDF：优先走 MinerU；高保真失败后再走本地结构化 fallback；最后才是纯文本兜底
- DOCX：优先走 `Word -> PDF -> MinerU / PDF 预处理`
- DOC：必须依赖 LibreOffice 转 PDF，否则直接失败

所有 reviewer 都共享同一份 evidence bundle，不要让每个 reviewer 各自重复解析原文。

### 2. 先做研究范式分类

在 reviewer 开始前，必须根据共享 evidence 生成研究范式分类：

- 输出文件：`evidence/manuscript_classification.json`
- 分类参考：`references/paradigm_criteria.yaml`
- 分类层级：先判定 `coarse_family`，再列出一个或多个 `paradigm_labels`
- 每个标签必须包含 `confidence` 和 `evidence_refs`
- mixed / uncertain 论文允许多标签，不要强行单标签归类

分类用于决定“该用什么方法标准审稿”。不能因为论文是理论、形式化、综述或叙事综合，就把方法审查写成 `N/A`。例如：

- 理论/形式化论文：审假设、推导、证明/模型逻辑、边界条件
- 博弈/数学推导论文：审策略空间、收益结构、均衡概念、推导严谨性
- 综述/元分析论文：审检索、纳入排除、证据综合、质量评估
- 实证论文：审识别策略、数据、测量、稳健性、推断边界

如果分类低置信度，写明不确定性，并要求 reviewer 使用论文自身呈现的方法范式来审，不要套用单一实证模板。

### 3. 并行启动 8 个 reviewer

固定角色是：

- 3 个 generalist reviewers
- 5 个 specialist reviewers

每个 reviewer 都必须：

- 只基于共享 evidence bundle 工作
- 返回结构化 JSON
- 不直接写最终报告
- 显式写明 `review_source`，只能是 `subagent` 或 `local`
- 明确参考 `manuscript_classification.json`，按论文实际范式审方法

在 `codex` 模式下，8 个 reviewer subagent 默认继承当前 Codex / OMX 的 frontier 或标准模型配置；不要硬编码过期模型名。reviewer 工作应使用较高推理强度（例如 high / xhigh，按当前环境支持项选择）。除非用户明确要求更快/更便宜的配置，否则不要把 committee reviewer 默认降到轻量模型，也不要省略推理强度设置让它回退到低思考强度。

如果平台一次性并发上限不足 8 个 reviewer，例如只能先拉起 6 个，那么先启动可用槽位，等有 reviewer 完成后再补齐剩余槽位。不要因为平台并发上限而中断整次审稿。

### 4. 主对话作为 editor

主对话负责：

- 合并重复 concern
- 区分共识与分歧
- 过滤掉没有证据支撑的 critical issue
- 降权或移除范式错配的 concern，例如用实证识别策略否定纯理论模型
- 结合期刊要求做最终判断
- 输出 final report / meta review / concerns table

### 5. 产物写入

所有输出统一写到项目配置里的 `DATA_DIR`。默认就是：

`fusion-reviewer/review_outputs/<run_label>/`

除此之外，系统还会自动生成一个更好找的固定入口：

`fusion-reviewer/review_outputs/按论文查看/<论文名>/最新结果/`

你以后找结果时，优先去 `按论文查看` 下面找，不需要记住每次 run 的长目录名。

## 期刊要求输入

如果用户提供了“期刊风格 / 审稿标准 / 栏目要求”，要一并纳入审稿。

做法：

- 预处理阶段把文本或文件写成 `evidence/journal_requirements.md`
- 8 个 reviewer 和 editor 都同时参考这份内容
- 最终报告里区分：
  - 论文本身的问题
  - 与目标期刊不匹配的问题

## 结果目录怎么找

每次完成后，会有两套目录：

1. 真正的运行目录（规范程序化文件名，供工具链读写）  
   `review_outputs/<run_label>/`
   
   核心文件：`final_report.md`、`meta_review.md`、`concerns_table.csv`、`final_summary.json`、`reviews/`

2. 更适合人直接打开的目录（友好中文短别名，硬链接指向运行目录，不额外占空间）  
   `review_outputs/按论文查看/<论文名>/最新结果/`

`最新结果` 目录里固定有这些短文件名：

- `00-结果说明.txt`
- `01-审稿总报告.md`（→ `final_report.md`）
- `02-审稿总报告.pdf`（→ `final_report.pdf`）
- `03-元审稿.md`（→ `meta_review.md`）
- `04-问题汇总.csv`（→ `concerns_table.csv`）
- `05-运行摘要.json`（→ `final_summary.json`）
- `07-提取诊断.json`（→ `evidence/diagnostics.json`）
- `08-期刊要求.md`（→ `evidence/journal_requirements.md`）
- `10-Reviewer逐份意见/`（→ `reviews/`）

返修稿还会额外出现 `06-返修回应审稿.md`。

其中 `10-Reviewer逐份意见/` 里会按固定命名保存 8 位 reviewer 的独立意见，例如：

- `11-委员会审稿-A.md`
- `12-委员会审稿-B.md`
- `13-委员会审稿-C.md`
- `21-理论专家审稿.md`
- `22-方法专家审稿.md`
- `23-表达专家审稿.md`
- `24-意义专家审稿.md`
- `25-结构专家审稿.md`

运行目录本身不再生成中文别名副本（`01-审稿总报告.md` 等），所有人类可读入口统一在 `按论文查看/` 下。

## 实操规则

- reviewer 之间彼此独立，不互相读取对方输出
- reviewer 共享 evidence，但不共享结论
- reviewer 和 editor 都必须参考 `evidence/manuscript_classification.json`
- 方法审查必须范式适配，理论/形式化/综述论文也要审对应的方法逻辑，不能写成 `N/A`
- 如果某个 reviewer 失败，不要让整次运行中断
- 最终报告必须显式标明缺失 reviewer slot
- 如果最终只有 6 个真实 subagent reviewer + 2 个主线程本地 reviewer，报告里必须明确写成 `6 个 subagent + 2 个 local`，不能表述成完整的 `8 个 subagent reviewer`
- 如果文档提取保真度降级，必须在最终报告里提醒用户
- 如果是 Word 且无法可靠转 PDF，优先明确说明是 fidelity 风险，不要假装版面信息可靠

## 推荐命令

推荐统一用 conda 环境运行，不要手动拼 `PYTHONPATH`：

如果 skill 已经安装到 Codex skills 目录，先在 PowerShell 中定位 skill：

```powershell
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$SkillRoot = Join-Path $CodexHome "skills\paper-review-committee"
```

如果你是在 `fusion-reviewer` 仓库里开发，也可以直接使用仓库内的 `.\codex-skill\scripts\...` 路径。

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\prepare_paper.py --paper .\paper.pdf
```

安装版等价命令：

```powershell
conda run -n review-fusion-py313 python "$SkillRoot\scripts\prepare_paper.py" --paper .\paper.pdf
```

如果带期刊要求：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\prepare_paper.py --paper .\paper.pdf --journal-file .\journal_requirements.txt
```

安装版等价命令：

```powershell
conda run -n review-fusion-py313 python "$SkillRoot\scripts\prepare_paper.py" --paper .\paper.pdf --journal-file .\journal_requirements.txt
```

如果是返修稿，预处理时同时传入返修说明和上一轮审稿：

```powershell
conda run -n review-fusion-py313 python "$SkillRoot\scripts\prepare_paper.py" --paper .\revised_paper.docx --revision-file .\response_letter.docx --previous-review-dir .\previous_review_outputs
```

如需自定义输出根目录，可显式传：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\prepare_paper.py --paper .\paper.pdf --output-root D:\somewhere\review_outputs
```

不传 `--output-root` 时，会默认使用 `fusion-reviewer` 配置里的 `DATA_DIR`。

如果 committee reviewer 已经把各自 JSON 写进了 `reviews/`，后续收口默认直接用正式 CLI：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-reviews --run-dir .\review_outputs\<run_label>
```

返修回应审稿需要模型调用时，补充 provider profile：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-reviews --run-dir .\review_outputs\<run_label> --provider-profile <provider_profile>
```

如果当前环境还没装好 `fusion-review` 命令，再退回到脚本入口：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\finalize_run.py --run-dir .\review_outputs\<run_label>
```

安装版脚本入口：

```powershell
conda run -n review-fusion-py313 python "$SkillRoot\scripts\finalize_run.py" --run-dir .\review_outputs\<run_label>
```

安装版返修收口脚本入口：

```powershell
conda run -n review-fusion-py313 python "$SkillRoot\scripts\finalize_run.py" --run-dir .\review_outputs\<run_label> --provider-profile <provider_profile>
```

如果已经有人工编辑过的 `editor_input.json`，可以额外传：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-reviews --run-dir .\review_outputs\<run_label> --editor-file .\review_outputs\<run_label>\editor_input.json
```

或：

```powershell
conda run -n review-fusion-py313 python .\codex-skill\scripts\finalize_run.py --run-dir .\review_outputs\<run_label> --editor-file .\review_outputs\<run_label>\editor_input.json
```

一旦 `reviews/` 目录已经存在，主对话就应直接使用上面的正式入口收口。除非你正在修正式 finalize 本身，否则不要临时创建 `tmp_finalize_*.py`、`paper_committee_run_*.py`、`rebuild_final_review.py` 这类一次性脚本来拼 reviewer 结果。

如果 reviewer 结果和 editor 结论目前只整理成了两个临时 UTF-8 JSON 文件，还没有正式写回 run 目录，也优先用正式 CLI：

```powershell
conda run -n review-fusion-py313 fusion-review codex finalize-from-payloads --run-dir .\review_outputs\<run_label> --reviews-file .\tmp_reviews.json --editor-file .\tmp_editor.json
```

这条命令会自动把 payload 写回 `reviews_input.json` / `editor_input.json` 并生成最终产物。优先使用它，而不是再临时写 `tmp_write_reviews_finalize.py` 之类的写盘脚本。

## 本地辅助脚本

- `scripts/prepare_paper.py`：准备共享证据包，并输出环境自检信息
- `fusion-review codex finalize-from-reviews --run-dir ...`：正式收口入口；直接从 `reviews/` 回收 reviewer JSON，自动重建 `reviews_input.json` / `editor_input.json` 并生成最终产物
- `fusion-review codex finalize-from-payloads --run-dir ... --reviews-file ... --editor-file ...`：如果 reviewer/editor 结果已经整理成临时 JSON 文件，用这个正式入口直接写回并收口
- `scripts/finalize_run.py --run-dir ...`：CLI 不可用时的脚本入口；默认行为与正式 CLI 对齐，也不需要手填 title/source-name
- `scripts/install_skill.py`：把仓库内的 skill 源码同步到 Codex skills 目录
- `references/paradigm_criteria.yaml`：研究范式分类标准，确保方法审查按论文范式而不是固定实证模板执行

## 参考文件

- `references/roles.yaml`：角色定义、focus areas、editor 职责
- `references/artifact_contract.md`：产物目录结构与必备文件
- `references/paradigm_criteria.yaml`：分类标签和每类论文对应/不对应的审查标准
