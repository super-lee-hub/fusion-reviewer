# Artifact Contract

这个 skill 会把审稿产物写到两层目录：

1. 真正的运行目录  
   `DATA_DIR/<run_label>/`

2. 更适合人工查找的目录  
   `DATA_DIR/按论文查看/<论文名>/最新结果/`

默认情况下，`DATA_DIR` 就是：

`fusion-reviewer/review_outputs`

## 必备目录结构

运行目录只保留规范程序化文件名（方便工具链读写）；人类友好的短别名统一放在 `按论文查看/` 下，通过硬链接指向同一份文件，不占用额外磁盘空间。

```text
review_outputs/
  <run_label>/
    evidence/
      normalized.md
      plain_text.txt
      page_index.json
      diagnostics.json
      structured.json
      manuscript_classification.json
      journal_requirements.md
      revision_notes.md
      previous_review.md
      source_copy.*
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
    revision_response_review.md
    revision_response_review.json
    reviews_input.json
    editor_input.json
    00-结果说明.txt

  按论文查看/
    <论文名>/
      最新结果/
        00-结果说明.txt
        01-审稿总报告.md          ← 硬链接 → <run_label>/final_report.md
        02-审稿总报告.pdf         ← 硬链接 → <run_label>/final_report.pdf
        03-元审稿.md              ← 硬链接 → <run_label>/meta_review.md
        04-问题汇总.csv            ← 硬链接 → <run_label>/concerns_table.csv
        05-运行摘要.json           ← 硬链接 → <run_label>/final_summary.json
        06-返修回应审稿.md         ← 硬链接 → <run_label>/revision_response_review.md
        07-提取诊断.json           ← 硬链接 → <run_label>/evidence/diagnostics.json
        08-期刊要求.md             ← 硬链接 → <run_label>/evidence/journal_requirements.md
        10-Reviewer逐份意见/
          11-委员会审稿-A.json/.md
          12-委员会审稿-B.json/.md
          13-委员会审稿-C.json/.md
          21-理论专家审稿.json/.md
          22-方法专家审稿.json/.md
          23-表达专家审稿.json/.md
          24-意义专家审稿.json/.md
          25-结构专家审稿.json/.md
```

## 文件含义

- `evidence/normalized.md`：供 reviewer 共享的归一化论文文本
- `evidence/page_index.json`：页码到行号的索引，用于 evidence refs
- `evidence/diagnostics.json`：记录提取路径、MinerU 是否成功、OCR 状态、保真度告警等
- `evidence/manuscript_classification.json`：研究范式分类结果，用于选择方法审查标准
- `evidence/journal_requirements.md`：如果用户提供了期刊要求，就统一写到这里
- `evidence/revision_notes.md`：返修说明 / 作者答复；返修审稿时必须存在且可读
- `evidence/previous_review.md`：上一轮审稿意见或系统从上一轮目录中选出的审稿文件
- `reviews/*.json`：每个 reviewer 的结构化输出
- `10-Reviewer逐份意见/`：给人直接查看的 reviewer 文档目录
- `meta_review.*`：editor / meta-review 结果
- `concerns_table.*`：去重合并后的 concerns
- `final_report.md`：最终给人读的完整审稿报告
- `final_summary.json`：给程序读的摘要
- `final_report.pdf`：可选 PDF 报告；当源文档或归一化文档可转成 PDF 时启用
- `revision_response_review.md/.json`：可选；返修稿且收口阶段提供可用 `provider_profile` 时生成

## Reviewer JSON 必备字段

每个 reviewer JSON 都应该包含：

- `agent_id`
- `kind`
- `review_source`
- `title`
- `provider_profile` 或 `mode`
- `model`
- `summary`
- `strengths`
- `weaknesses`
- `recommendation`
- `findings`

其中：

- `review_source=subagent` 表示真实独立子代理产物
- `review_source=local` 表示主线程本地补写产物

如果 `review_source=local`，最终报告必须把它和真实 subagent reviewer 分开统计，不能直接并称为完整的 8 个 subagent reviewer。

每条 finding 至少要有：

- `issue_key`
- `title`
- `description`
- `category`
- `severity`
- `evidence_refs`
- `recommendation`

## 研究范式分类 JSON

`evidence/manuscript_classification.json` 应使用 UTF-8，并包含：

- `coarse_family`：`empirical`、`theoretical`、`mixed` 或 `review_synthesis`
- `paradigm_labels`：一个或多个标签对象
- `rationale`：分类依据，必须指向论文证据

每个 `paradigm_labels[]` 对象至少包含：

- `label`：来自 `references/paradigm_criteria.yaml` 的标签，例如 `formal_modeling`、`experiment`、`meta_analysis`
- `confidence`：0 到 1 之间的小数
- `primary`：是否为主标签；正常情况下只有一个 `true`
- `evidence_refs`：支持该分类的页码、行号或引用片段

分类只决定“用什么方法标准审”，不能把理论、形式化、综述类论文的方法审查写成 `N/A`。低置信度或混合论文应保留多个标签，并让 editor 在合成时标记不确定性。

## 合并规则

- 先按 `canonical issue key` 合并
- 如果没有稳定 key，再看 `evidence overlap` 和 `normalized title`
- 合并后的 concern 要保留所有 `raised_by`
- 没有 evidence reference 的 finding 不能直接进入最终 critical issues
- 与 `manuscript_classification.json` 明显不匹配的 finding 应进入分歧或降权，不应直接成为 consensus critical issue

## Codex 与 backend 的一致性要求

- `codex` 模式和 `backend` 模式尽量输出同名文件
- schema 尽量保持一致
- UI、CLI、Codex skill 都应该能读取同一份目录结构
