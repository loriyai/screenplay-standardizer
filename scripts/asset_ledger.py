#!/usr/bin/env python3
"""Initialize, validate, and render a screenplay asset ledger."""

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


HEADING = re.compile(r"场景(\d+)：(日|夜)·(内|外)·(.+)")
SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt")
ID_PATTERNS = {
    "scenes": re.compile(r"SC\d{4,}"),
    "characters": re.compile(r"CHAR\d{4,}"),
    "locations": re.compile(r"LOC\d{4,}"),
    "props": re.compile(r"PROP\d{4,}"),
}
GENERIC_ROLE = re.compile(r"^(?:宾客|同学|女生|男生|医生|男医生|女医生|护士|保镖|工作人员|服务员|路人|司机|助理|店员|老师|教授|大爷|阿姨|侍者|主持人)")


def split_cast_entry(entry: str) -> tuple[str, str]:
    """Keep a source-backed group name such as 百姓-群像 intact."""
    if "-群像-" in entry:
        prefix, image = entry.split("-群像-", 1)
        name = prefix + "-群像"
    else:
        name, image = entry.split("-", 1)
    name, image = name.strip(), image.strip()
    if not name or not image:
        raise ValueError(f"人物条目缺少名称或形象：{entry}")
    return name, image


def source_paragraphs(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = data["paragraphs"]
    if any(item["id"] != i for i, item in enumerate(paragraphs, 1)):
        raise ValueError("source.json 段落编号不连续")
    return [item["text"] for item in paragraphs]


def digest(paragraphs: list[str]) -> str:
    packed = json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def parse_script(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    scenes = []
    heading_positions = []
    for i, line in enumerate(lines):
        match = HEADING.fullmatch(line.strip())
        if not match:
            continue
        number = int(match.group(1))
        if number != len(scenes) + 1:
            raise ValueError(f"剧本场景序号不连续：{number}")
        if i + 1 >= len(lines) or not lines[i + 1].startswith("人物："):
            raise ValueError(f"场景{number} 缺少紧随标题的人物行")
        cast_line = lines[i + 1].strip()
        if not cast_line.endswith("；"):
            raise ValueError(f"场景{number} 人物行未以分号结束")
        if cast_line == "人物：无；":
            entries = []
            names = []
        else:
            entries = cast_line[3:-1].split("；")
            if any("-" not in entry for entry in entries):
                raise ValueError(f"场景{number} 人物行条目缺少形象字段")
            names = [split_cast_entry(entry)[0] for entry in entries]
            if any(not name for name in names) or len(names) != len(set(names)):
                raise ValueError(f"场景{number} 人物名称为空或重复")
        scenes.append({
            "number": number,
            "heading": line.strip(),
            "location_name": match.group(4).strip(),
            "cast_entries": entries,
            "cast_names": names,
        })
        heading_positions.append(i)
    if not scenes:
        raise ValueError("标准剧本没有可识别的场景")
    for index, scene in enumerate(scenes):
        end = heading_positions[index + 1] if index + 1 < len(scenes) else len(lines)
        scene["body_text"] = "\n".join(lines[heading_positions[index] + 2:end])
    return scenes


def scene_ranges(workdir: Path, count: int, source_count: int) -> list[list[int]]:
    found = []
    for path in (workdir / "scenes").glob("scene_*.txt"):
        match = SCENE_FILE.fullmatch(path.name)
        if not match:
            raise ValueError(f"逐场文件名不符合格式：{path.name}")
        found.append(tuple(int(part) for part in match.groups()))
    found.sort()
    if len(found) != count:
        raise ValueError("逐场中间稿数量与标准剧本场景数不一致")
    expected = 1
    ranges = []
    for number, start, end in found:
        if number != len(ranges) + 1 or start != expected or end < start:
            raise ValueError(f"场景{number} 的原文段落范围不连续")
        ranges.append([start, end])
        expected = end + 1
    if expected != source_count + 1:
        raise ValueError("逐场中间稿未覆盖全部原文段落")
    return ranges


def fact_list(records: list[dict], paragraphs: list[str], scenes_by_id: dict, label: str,
              required_scene: str | None = None) -> None:
    for fact in records:
        if not isinstance(fact, dict):
            raise ValueError(f"{label} 事实必须是对象")
        for key in ("text", "evidence", "attribution"):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                raise ValueError(f"{label} 事实缺少 {key}")
        refs = fact.get("source_paragraphs")
        scene_ids = fact.get("scene_ids")
        if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, int) or isinstance(ref, bool) or ref < 1 or ref > len(paragraphs)
            for ref in refs
        ):
            raise ValueError(f"{label} 原文段落编号无效")
        if not isinstance(scene_ids, list) or not scene_ids or len(scene_ids) != len(set(scene_ids)):
            raise ValueError(f"{label} 事实缺少有效的 scene_ids")
        if required_scene and scene_ids != [required_scene]:
            raise ValueError(f"{label} 逐场事实只能关联所在场景")
        for scene_id in scene_ids:
            scene = scenes_by_id.get(scene_id)
            if not scene:
                raise ValueError(f"{label} 引用了不存在的场景 {scene_id}")
            start, end = scene["source_range"]
            if not any(start <= ref <= end for ref in refs):
                raise ValueError(f"{label} 在场景 {scene_id} 中没有原文证据")
        evidence = "".join(fact["evidence"].split())
        original = "".join("".join(paragraphs[ref - 1] for ref in refs).split())
        if evidence not in original:
            raise ValueError(f"{label} 的证据未出现在所引原文段落中：{fact['evidence']}")


def validate(script: list[dict], paragraphs: list[str], data: dict) -> None:
    if data.get("schema_version") != 2:
        raise ValueError("不支持的资产台账 schema_version")
    if not isinstance(data.get("pending_reviews", []), list):
        raise ValueError("pending_reviews 必须是数组")
    if data.get("pending_reviews"):
        raise ValueError("台账仍有改稿迁移待核项；请处理后再生成资产 TXT")
    if data.get("source_digest") != digest(paragraphs):
        raise ValueError("资产台账与当前原文版本不匹配")
    for category in ID_PATTERNS:
        if not isinstance(data.get(category), list):
            raise ValueError(f"资产台账缺少 {category} 数组")
    if len(data["scenes"]) != len(script):
        raise ValueError("台账场景数与标准剧本不一致")
    all_ids = set()
    for category, pattern in ID_PATTERNS.items():
        for item in data[category]:
            asset_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(asset_id, str) or not pattern.fullmatch(asset_id):
                raise ValueError(f"{category} 存在无效 ID：{asset_id}")
            if asset_id in all_ids:
                raise ValueError(f"重复的资产 ID：{asset_id}")
            all_ids.add(asset_id)
    retired = data.get("retired_ids", [])
    if not isinstance(retired, list) or len(retired) != len(set(retired)) or any(
        not isinstance(value, str) or value in all_ids for value in retired
    ):
        raise ValueError("retired_ids 无效或与现用 ID 冲突")
    inactive = data.get("inactive_assets", {"characters": []})
    if not isinstance(inactive, dict) or not isinstance(inactive.get("characters"), list):
        raise ValueError("inactive_assets 无效")
    for item in inactive["characters"]:
        asset_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(asset_id, str) or not ID_PATTERNS["characters"].fullmatch(asset_id) or asset_id in all_ids or asset_id in retired:
            raise ValueError(f"待确认人物 ID 冲突或无效：{asset_id}")
        all_ids.add(asset_id)
    if inactive["characters"]:
        raise ValueError("仍有可能改名或退场的人物；请确认身份后恢复关联或确认退场")
    scenes_by_id = {item["id"]: item for item in data["scenes"]}
    characters = {item["id"]: item for item in data["characters"]}
    locations = {item["id"]: item for item in data["locations"]}
    props = {item["id"]: item for item in data["props"]}
    prop_primary_seen = {item["id"]: False for item in data["props"]}
    location_heading_names = {item["id"]: set() for item in data["locations"]}
    cast_union = {entry for item in script for entry in item["cast_entries"]}
    for item in data["characters"]:
        if item.get("name") not in cast_union:
            raise ValueError(f"人物资产名称不在任何人物行中：{item.get('name')}")
    for category in ("characters", "locations", "props"):
        for item in data[category]:
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError(f"{item['id']} 缺少名称")
            if not isinstance(item.get("aliases"), list) or any(
                not isinstance(alias, str) or not alias.strip() for alias in item["aliases"]
            ):
                raise ValueError(f"{item['id']} 别名字段无效")
            facts = item.get("visual_facts")
            if not isinstance(facts, list):
                raise ValueError(f"{item['id']} 缺少 visual_facts 数组")
            fact_list(facts, paragraphs, scenes_by_id, item["id"])
            traits = []
            if category == "characters":
                traits = item.get("personality_facts")
                if not isinstance(traits, list):
                    raise ValueError(f"{item['id']} 缺少 personality_facts 数组")
                fact_list(traits, paragraphs, scenes_by_id, item["id"])
            linked_field = {"characters": "character_ids", "locations": "location_id", "props": "prop_ids"}[category]
            for fact in facts + traits:
                for scene_id in fact["scene_ids"]:
                    scene = scenes_by_id[scene_id]
                    linked = scene[linked_field]
                    if item["id"] not in (linked if isinstance(linked, list) else [linked]):
                        raise ValueError(f"{item['id']} 的事实关联场景 {scene_id}，但资产未在该场出现")
    expected_start = 1
    for base, scene in zip(script, data["scenes"]):
        if scene.get("number") != base["number"] or scene.get("heading") != base["heading"]:
            raise ValueError(f"场景{base['number']} 与标准剧本不一致")
        source_range = scene.get("source_range")
        if not isinstance(source_range, list) or len(source_range) != 2 or any(
            not isinstance(value, int) or isinstance(value, bool) for value in source_range
        ):
            raise ValueError(f"场景{base['number']} 原文范围无效")
        start, end = source_range
        if start != expected_start or end < start:
            raise ValueError(f"场景{base['number']} 原文范围不连续")
        expected_start = end + 1
        location = locations.get(scene.get("location_id"))
        if not location or base["location_name"] not in [location["name"], *location["aliases"]]:
            raise ValueError(f"场景{base['number']} 地点关联缺失或名称不匹配")
        location_heading_names[location["id"]].add(base["location_name"])
        char_ids, prop_ids = scene.get("character_ids"), scene.get("prop_ids")
        if not isinstance(char_ids, list) or len(char_ids) != len(set(char_ids)) or any(
            ref not in characters for ref in char_ids
        ):
            raise ValueError(f"场景{base['number']} 人物关联无效")
        if not isinstance(prop_ids, list) or len(prop_ids) != len(set(prop_ids)) or any(
            ref not in props for ref in prop_ids
        ):
            raise ValueError(f"场景{base['number']} 道具关联无效")
        linked_names = [characters[ref]["name"] for ref in char_ids]
        if linked_names != base["cast_entries"]:
            raise ValueError(f"场景{base['number']} 人物资产名称及顺序须逐字对应人物行完整条目")
        body = "".join(base["body_text"].split())
        for ref in prop_ids:
            item = props[ref]
            variants = [item["name"], *item["aliases"]]
            if not any("".join(value.split()) in body for value in variants):
                raise ValueError(f"场景{base['number']} 道具 {ref} 的名称或别名未出现在该场剧本正文中")
            if "".join(item["name"].split()) in body:
                prop_primary_seen[ref] = True
        if not isinstance(scene.get("visual_facts"), list):
            raise ValueError(f"场景{base['number']} 缺少 visual_facts 数组")
        fact_list(scene["visual_facts"], paragraphs, scenes_by_id, scene["id"], scene["id"])
        basis = scene.get("time_basis")
        if not isinstance(basis, dict) or basis.get("kind") not in ("原文明示", "格式默认"):
            raise ValueError(f"场景{base['number']} 须确认时间依据")
        refs = basis.get("source_paragraphs")
        if not isinstance(refs, list) or any(not isinstance(ref, int) or isinstance(ref, bool) or ref < start or ref > end for ref in refs):
            raise ValueError(f"场景{base['number']} 时间证据段落无效")
        if (basis["kind"] == "原文明示" and not refs) or (basis["kind"] == "格式默认" and refs):
            raise ValueError(f"场景{base['number']} 时间依据与证据段落不一致")
    if expected_start != len(paragraphs) + 1:
        raise ValueError("台账场景范围未覆盖全部原文")
    for ref, seen in prop_primary_seen.items():
        if not seen:
            raise ValueError(f"道具 {ref} 的主名称未出现在关联场景的剧本正文中")
    for ref, names in location_heading_names.items():
        if names and locations[ref]["name"] not in names:
            raise ValueError(f"地点 {ref} 的主名称未出现在关联场景标题中")


def initialize(script_path: Path, workdir: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("台账已存在；请沿用旧 ID，不要重新初始化覆盖")
    script = parse_script(script_path)
    paragraphs = source_paragraphs(workdir / "source.json")
    ranges = scene_ranges(workdir, len(script), len(paragraphs))
    character_ids = {}
    characters, locations, scenes = [], [], []
    for base, source_range in zip(script, ranges):
        location_name = base["location_name"]
        # Identical generic headings can denote different places. Start separately;
        # merge only after checking the original scene context.
        location_id = f"LOC{len(locations) + 1:04d}"
        locations.append({"id": location_id, "name": location_name, "aliases": [], "visual_facts": []})
        linked = []
        for entry in base["cast_entries"]:
            role_name, _ = split_cast_entry(entry)
            key = (entry, base["number"]) if GENERIC_ROLE.match(role_name) or role_name.endswith("-群像") else (entry, None)
            if key not in character_ids:
                asset_id = f"CHAR{len(characters) + 1:04d}"
                character_ids[key] = asset_id
                characters.append({
                    "id": asset_id, "name": entry, "aliases": [],
                    "visual_facts": [], "personality_facts": [],
                })
            linked.append(character_ids[key])
        scenes.append({
            "id": f"SC{base['number']:04d}", "number": base["number"],
            "heading": base["heading"], "source_range": source_range,
            "location_id": location_id, "character_ids": linked,
            "prop_ids": [], "visual_facts": [],
            "time_basis": {"kind": "待核", "source_paragraphs": []},
        })
    data = {
        "schema_version": 2, "source_digest": digest(paragraphs),
        "scenes": scenes, "characters": characters, "locations": locations,
        "props": [], "retired_ids": [], "inactive_assets": {"characters": []}, "pending_reviews": [],
    }
    # The index is a draft until each scene's time basis is checked.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已建立 {len(scenes)} 场、{len(characters)} 人物、{len(locations)} 地点的空事实台账：{output}")


def render_fact(fact: dict) -> str:
    refs = "、".join(str(value) for value in fact["source_paragraphs"])
    return f"{fact['text']}（{fact['attribution']}；原文段落 {refs}：{fact['evidence']}）"


ATMOSPHERE = re.compile(r"黑|暗|光|雷|雨|雪|雾|夜色|暖色|潮湿|微凉|明亮")


def visual_text(facts: list[dict]) -> list[str]:
    return list(dict.fromkeys(fact["text"].strip().rstrip("。") for fact in facts))


def render(script_path: Path, source_path: Path, ledger_path: Path, output: Path) -> None:
    script = parse_script(script_path)
    paragraphs = source_paragraphs(source_path)
    data = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    validate(script, paragraphs, data)
    characters = {item["id"]: item for item in data["characters"]}
    locations = {item["id"]: item for item in data["locations"]}
    props = {item["id"]: item for item in data["props"]}
    lines = ["角色列表"]
    for item in data["characters"]:
        features = "、".join(visual_text(item["visual_facts"])) or "外观未明示"
        lines.append(f"- {item['name']}：{features}。")

    lines.extend(["", "场景列表"])
    for scene in data["scenes"]:
        loc = locations[scene["location_id"]]
        scene_facts = visual_text(scene["visual_facts"])
        atmosphere = [part for part in scene_facts if ATMOSPHERE.search(part)]
        elements = [part for part in scene_facts if part not in atmosphere]
        elements += visual_text(loc["visual_facts"])
        elements += [props[ref]["name"] for ref in scene["prop_ids"]]
        elements = list(dict.fromkeys(elements))
        if not elements:
            elements = [script[scene["number"] - 1]["location_name"]]
        lines.append(f"- {scene['heading']}｜氛围：{'、'.join(atmosphere) or '未明示'}；关键元素：{'、'.join(elements)}。")

    lines.extend(["", "主要道具列表"])
    for item in data["props"]:
        features = "、".join(visual_text(item["visual_facts"])) or "外形未明示"
        lines.append(f"- {item['name']}：{features}。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=output.parent, delete=False) as temp:
        temp.write("\n".join(lines) + "\n")
        temp_path = Path(temp.name)
    try:
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    print(f"已验证并生成资产 TXT：{output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="从剧本和逐场稿建立空事实资产索引")
    init.add_argument("script", type=Path)
    init.add_argument("workdir", type=Path)
    init.add_argument("--output", type=Path, required=True)
    view = commands.add_parser("render", help="验证台账并生成易读资产 TXT")
    view.add_argument("script", type=Path)
    view.add_argument("source", type=Path)
    view.add_argument("ledger", type=Path)
    view.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.script, args.workdir, args.output)
        else:
            render(args.script, args.source, args.ledger, args.output)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
