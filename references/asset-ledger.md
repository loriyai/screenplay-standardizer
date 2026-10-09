# 资产台账结构

后台主文件使用 UTF-8 JSON；分类视觉描述 TXT 由它生成。标准剧本的场景序号用于显示，`SC`、`LOC`、`CHAR`、`PROP` ID 用于长期关联。首次建立台账可用 `asset_ledger.py init` 生成无事实的索引骨架；若已有台账，先用 `asset_migrate.py` 迁移并审核，不重新初始化或重编号。`init` 生成的 `time_basis: 待核` 是草稿，须逐场确认后才能渲染。

```text
python scripts/asset_ledger.py init <standard-script.txt> <task-work-dir> --output <asset-ledger.json>
python scripts/asset_ledger.py render <standard-script.txt> <task-work-dir>/source.json <asset-ledger.json> --output <asset-catalog.txt>
```

## JSON 字段

```json
{
  "schema_version": 2,
  "source_digest": "由 init 计算的原文段落摘要",
  "scenes": [
    {
      "id": "SC0001",
      "number": 1,
      "heading": "场景1：日·内·昏暗房间",
      "source_range": [1, 19],
      "location_id": "LOC0001",
      "character_ids": ["CHAR0001"],
      "prop_ids": [],
      "visual_facts": [],
      "time_basis": {"kind": "原文明示", "source_paragraphs": [1]}
    }
  ],
  "characters": [
    {
      "id": "CHAR0001",
      "name": "卢筱筱-基础形象",
      "aliases": [],
      "visual_facts": [],
      "personality_facts": []
    }
  ],
  "locations": [
    {"id": "LOC0001", "name": "昏暗房间", "aliases": [], "visual_facts": []}
  ],
  "props": [
    {"id": "PROP0001", "name": "枪", "aliases": [], "visual_facts": []}
  ],
  "retired_ids": [],
  "inactive_assets": {"characters": []},
  "pending_reviews": []
}
```

`init` 只建立场景、人物和地点索引，不自动提取资产事实或主要道具。地点按场景先分配独立 ID，核实是同一地点后再合并；明确姓名的角色只按完整人物条目初步归并。`卢筱筱-基础形象` 与 `卢筱筱-黑色裙子` 分别建立稳定 ID；同一完整条目在多场出现且身份明确时复用 ID。泛称角色及 `称谓-群像` 按场景分开，仍需人工核实。群像只用于无姓名、无台词且原文明示阵营、身份、人设或职业的背景人物。若相同条目指向不同实体，拆成不同 ID，并用逐场关联指向各自实体。新 ID 只追加，废弃的 ID 放入 `retired_ids`，不回收。场景的 `number`、`heading` 可随剧本修改；`id` 保持稳定。

名称取自标准剧本：人物 `name` 对应 `人物：` 行中的完整条目（角色名 + 简单形象描述），连字符和描述文字均须一致；场景 `heading` 对应完整标题；地点主名称对应至少一个关联场景标题中的地点字样，其他已确认同一地点的标题字样可放入 `aliases`；主要道具 `name` 必须原样出现在至少一个关联场景的正文，每个关联场景须出现主名称或该场使用的别名。不要把颜色、价格、用途或剧情说明拼成剧本里没有的道具名，这些信息放入事实字段。

`time_basis.kind` 只能是 `原文明示` 或 `格式默认`。前者的 `source_paragraphs` 必须列出该场原文明示时间的段落；后者须为空数组。标题中默认写 `日` 不代表原文明确是白天。脚本生成的待核值不能直接通过 `render`。

每条 `visual_facts` 或 `personality_facts` 采用以下结构。`text` 是简短事实，`evidence` 是原文中的连续原句或词组；证据跨段时，可引用各段共有的短词组，或拆成多条事实。`source_paragraphs` 使用 `prepare` 生成的原文段落编号。`scene_ids` 只列证据所在场景，不表示此特征在未明示的后续场景仍持续。

```json
{
  "text": "穿黑色裙子",
  "evidence": "穿着黑色裙子",
  "source_paragraphs": [27],
  "scene_ids": ["SC0002"],
  "attribution": "叙述"
}
```

人物性格只填原文直接命名的稳定特点。出自人物自述或他人评价时，`attribution` 填实际说话人并在 `text` 中写成“某人自述／某人认为……”，不把评价升级成客观结论。仅有一次温柔语气、一次发怒或某个行动时，`personality_facts` 保持空数组。性格也不写入剧本人物行的“形象”。

可记录的明示视觉信息包括：人物外貌、明显年龄、发型、衣着及配饰；地点布局、固定陈设、可见材质；道具形状、颜色、材质、相对大小、标记与状态。逐场临时的天气、光线、布置放在 `scenes[].visual_facts`。未知信息留空。`基础形象`、默认的 `日`、自动生成的 ID 和未出现的生图风格都不是原文事实。

道具只收录对剧情、人物动作或视觉识别有作用的主要物件。若原文无法确认两次提到的是同一件道具，保留不同 ID 或标记待核，不靠近义词自行合并。`prop_ids` 记录其出现的场景；服装通常作为人物视觉事实，不自动建为道具。

## 核对

`render` 检查：原文摘要、场景与段落范围、ID 引用、每场人物资产 `name` 列表与该场 `人物：` 行完整条目逐项、逐字、按顺序匹配、场景标题及地点名称与剧本一致、道具主名称和别名在关联场景正文中出现、资产事实的场景出现关系、证据在原文段落中出现。`pending_reviews` 或 `inactive_assets.characters` 未处理完时不能渲染。它无法判断摘要是否正确表达证据，也无法证明“性格”是否真的被原文明确写出；这些仍需人工逐条核对。

生成的 TXT 只保留三个标题及其列表：`角色列表`、`场景列表`、`主要道具列表`。角色以完整人物资产 `name` 为标签，列核心可见特征；场景以标准剧本标题为名，列有事实依据的氛围与关键元素；主要道具列核心可见特征。没有明示信息时写“未明示”，不从 `基础形象` 或格式默认时间补造。TXT 不展示后台 ID、出处段落、证据原句、别名表、逐场索引或性格栏；完整信息仍由 JSON 保存。

## 改稿迁移

先对新稿运行 `prepare`、逐场复核和 `assemble`，再迁移旧台账：

```text
python scripts/asset_migrate.py <old-ledger.json> <old-source.json> <new-script.txt> <new-workdir> --output <migrated-draft.json> --report <migration-report.json> [--mapping <mapping.json>]
```

脚本验证旧台账的原文摘要。原文内容完全相同且唯一对应的场景自动继承旧 `SC` ID；其他场景需在映射文件中给出旧 ID 对应的新场景号数组，删除则填 `null`。拆场或并场时用 `primary_ids` 明确哪个新场继续使用哪个旧 SC ID；拆场的其余新场填 `null`，由脚本分配新 ID。例如：

```json
{
  "scenes": {"SC0001": [1, 2], "SC0002": [3], "SC0003": [3], "SC0004": null},
  "primary_ids": {"1": "SC0001", "2": null, "3": "SC0002"},
  "characters": {"CHAR0007": "新人物行完整条目-形象描述"}
}
```

一对一场景不必填写 `primary_ids`。旧 SC ID 只能由一个新场继承；合并时其余旧 SC ID 进入 `retired_ids`，但其可定位的事实证据改关联至新 SC ID。新场景、新人物、新地点使用比所有旧 ID、暂存 ID 与废弃 ID 更大的编号。

旧证据只在对应新场的原文段落中唯一匹配且证据仍逐字出现时自动迁移。资产事实还必须关联到该资产实际出现的新场景；否则保留在迁移报告的待核项中。拆场的道具出现关系和拆并场的时间依据需要复核。场景标题的地点名发生变化时，先建立新 LOC ID 并报告身份待核；不能自动把新名称当作旧地点别名。

未改场景的原文段落序列完全一致时，证据编号按场景起点偏移直接迁移；只对改动场景重新搜索证据。迁移报告列出 `unchanged_source_scenes` 数量，方便判断复用范围。

旧人物完整条目没有在新剧本人物行出现时，完整记录先放入 `inactive_assets.characters`，同时加入 `pending_reviews`；这表示“可能改名、换形象或退场”，并不立即废弃人物 ID。若实际改名或原有形象条目更名，在映射中用 `characters` 指定旧 ID 对应的新人物行完整条目后重新迁移；若出现新形象，则分配新的 `CHAR` ID，不将旧形象 ID 改名挪用。若确认退场，人工把 ID 放入 `retired_ids`，移除暂存记录，并核对其旧事实是否需要保留为历史记录。处理所有待核项后才清空 `pending_reviews` 并渲染。旧版 `schema_version: 1` 可迁移，但每场 `time_basis` 都须复核。
