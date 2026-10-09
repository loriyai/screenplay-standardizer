# 逐场复核与人工确认

先运行 `screenplay_review.py`。报告的 `paragraph_map` 对每个原文段落列出对应的逐场稿行号；纯角色名、独立 OS/画外音、空段等注明具体原因。`coverage_missing` 与 `coverage_extra` 必须处理。`speaker_items` 同时包含原文引号和无引号成稿台词，列出原文片段、成稿行、说话人提示及待核原因。没有明确提示时不得仅凭动作主语或“看向某人”自动认定说话人。

```text
python scripts/screenplay_review.py <workdir> --report <review.json> [--decisions <decisions.json>] [--previous-report <old-review.json>]
python scripts/screenplay_io.py assemble <workdir> --review-report <review.json> --output <script.txt>
```

人工决定用报告中的 `decision_key` 作键。复制同一条的 `decision_template`，确认后增加 `speaker`；原文引号是叙述中的引用、并非台词时增加 `classification: 非台词`，此时该引号不能已经匹配成稿台词。示意：

```json
{
  "报告中的 decision_key": {
    "source_text": "我在这里",
    "context_before": ["阿明回头"],
    "context_after": [],
    "draft_text": "我在这里。",
    "speaker": "阿明"
  }
}
```

台词字词必须仍与原文一致；`speaker` 决定仅确认归属，不豁免漏文、额外文字或台词差异。脚本会逐字比较决定中的原文片段、前后文、成稿台词；任何一项变化都会使旧决定失效，需重新确认。决定键由片段、相邻上下文及场景标题内容计算，不依赖原文段落号；只在内容和上下文仍匹配时复用。

原文中确属非剧情正文、但脚本未能自动识别的纯格式提示，可以在 `decisions.json` 的 `paragraphs` 对象中按该段 `decision_key` 写入报告中的 `decision_template`，并加具体 `reason`。不得用它跳过动作、台词或情节内容。该决定同样在原文和上下文改变后失效。

同一份决定文件也可包含上面的说话人确认和 `paragraphs`。改稿重跑时传入旧报告 `--previous-report`：原文场景内容、逐场稿内容及该场相关决定未变化的场景可直接复用检查结果，即使前面的段落增删导致编号平移。其余场景重新检查。`assemble` 强制核对当前原文和逐场稿摘要；旧报告不能用于变化后的文件。
