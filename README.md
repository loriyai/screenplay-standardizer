# screenplay-standardizer

版本：v0.01

将非标准中文剧本整理为统一格式的剧本 TXT，并从原文建立可追溯的资产台账 JSON 与分类视觉描述 TXT。

## 内容

- `SKILL.md`：技能说明和工作流程
- `scripts/screenplay_io.py`：提取 TXT/DOCX 原文、编号并合并逐场稿
- `scripts/screenplay_review.py`：核对原文覆盖、台词及说话人
- `scripts/asset_ledger.py`：建立、验证并渲染资产台账
- `scripts/asset_migrate.py`：改稿时迁移稳定资产 ID
- `references/`：复核决定和资产台账结构说明

## 使用

参照 [SKILL.md](SKILL.md) 的逐场流程。脚本使用 Python 标准库，无需安装额外依赖。示例命令：

```bash
python scripts/screenplay_io.py prepare input.docx --workdir work
python scripts/screenplay_review.py work --report work/review.json
python scripts/screenplay_io.py assemble work --review-report work/review.json --output outputs/standard-script.txt
python scripts/asset_ledger.py init outputs/standard-script.txt work --output work/asset-ledger.json
python scripts/asset_ledger.py render outputs/standard-script.txt work/source.json work/asset-ledger.json --output outputs/asset-catalog.txt
```

`init` 只生成待核骨架。填写原文证据、逐场关联及 `time_basis` 后，才能运行 `render`。
