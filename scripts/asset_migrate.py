#!/usr/bin/env python3
"""Migrate stable screenplay asset IDs through scene edits, splits, and merges."""

import argparse
import copy
import json
import re
from pathlib import Path

from asset_ledger import GENERIC_ROLE, digest, parse_script, scene_ranges, source_paragraphs, split_cast_entry


PREFIXES = {"scenes": "SC", "characters": "CHAR", "locations": "LOC", "props": "PROP"}


def compact(value):
    return "".join(char for char in value if char.isalnum())


def next_id(prefix, used):
    number = max((int(value[len(prefix):]) for value in used if re.fullmatch(prefix + r"\d+", value)), default=0) + 1
    result = f"{prefix}{number:04d}"
    used.add(result)
    return result


def scene_text(scene, paragraphs):
    start, end = scene["source_range"]
    return compact("".join(paragraphs[start - 1:end]))


def normalize_mapping(raw, old_scenes, new_signatures, old_paragraphs):
    requested = raw.get("scenes", {})
    if not isinstance(requested, dict):
        raise ValueError("mapping.scenes 必须是旧场景 ID 到新场景号数组或 null 的对象")
    old_ids = {scene["id"] for scene in old_scenes}
    if set(requested) - old_ids:
        raise ValueError("mapping.scenes 包含不存在的旧场景 ID")
    result, pending = {}, []
    for scene in old_scenes:
        scene_id = scene["id"]
        if scene_id in requested:
            value = requested[scene_id]
            numbers = [] if value is None else [value] if isinstance(value, int) and not isinstance(value, bool) else value
            if not isinstance(numbers, list) or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > len(new_signatures) for n in numbers) or len(numbers) != len(set(numbers)):
                raise ValueError(f"{scene_id} 的新场景号无效")
            result[scene_id] = numbers
        else:
            signature = scene_text(scene, old_paragraphs)
            exact = [i + 1 for i, candidate in enumerate(new_signatures) if candidate == signature]
            if len(exact) == 1:
                result[scene_id] = exact
            else:
                pending.append({"kind": "场景对应待核", "old_id": scene_id, "exact_candidates": exact})
    return result, pending


def select_primary(mapping, requested, new_count):
    incoming = {i: [] for i in range(1, new_count + 1)}
    for old_id, numbers in mapping.items():
        for number in numbers:
            incoming[number].append(old_id)
    primary_input = requested.get("primary_ids", {})
    if not isinstance(primary_input, dict):
        raise ValueError("mapping.primary_ids 必须是新场景号到旧 SC ID 的对象")
    primary, pending = {}, []
    for number, sources in incoming.items():
        specified = str(number) in primary_input
        chosen = primary_input.get(str(number))
        complex_lineage = len(sources) > 1 or any(len(mapping[sid]) > 1 for sid in sources)
        if specified and chosen is None:
            pass  # Split continuation starts a new SC ID.
        elif chosen is not None:
            if chosen not in sources:
                raise ValueError(f"新场景{number} 的主 ID 不在其旧场景来源中")
            primary[number] = chosen
        elif len(sources) == 1 and not complex_lineage:
            primary[number] = sources[0]
        elif complex_lineage:
            pending.append({"kind": "主场景 ID 待指定", "new_scene": number, "old_ids": sources})
    chosen = list(primary.values())
    if len(chosen) != len(set(chosen)):
        raise ValueError("一个旧 SC ID 不能成为多个新场景的主 ID")
    if set(primary_input) - {str(i) for i in incoming}:
        raise ValueError("primary_ids 包含无效新场景号")
    return incoming, primary, pending


def relocate_refs(refs, old_scene_ids, old_by_id, lineage, new_scenes_by_number, old_paragraphs, new_paragraphs, new_index):
    located = []
    for ref in refs:
        owners = [sid for sid in old_scene_ids if sid in old_by_id and old_by_id[sid]["source_range"][0] <= ref <= old_by_id[sid]["source_range"][1]]
        if len(owners) != 1:
            return None
        candidates = []
        needle = compact(old_paragraphs[ref - 1])
        if not needle:
            return None
        if len(lineage[owners[0]]) == 1:
            number = lineage[owners[0]][0]
            old_start, old_end = old_by_id[owners[0]]["source_range"]
            new_start, new_end = new_scenes_by_number[number]["source_range"]
            if old_paragraphs[old_start - 1:old_end] == new_paragraphs[new_start - 1:new_end]:
                located.append((number, new_start + ref - old_start))
                continue
        for number in lineage[owners[0]]:
            candidates.extend((number, i) for i in new_index[number].get(needle, []))
        if len(candidates) != 1:
            return None
        located.append(candidates[0])
    return located


def migrate_fact(fact, old_by_id, lineage, new_scenes_by_number, old_paragraphs, new_paragraphs, new_index):
    located = relocate_refs(fact["source_paragraphs"], fact["scene_ids"], old_by_id, lineage,
                            new_scenes_by_number, old_paragraphs, new_paragraphs, new_index)
    if not located:
        return None
    revised = copy.deepcopy(fact)
    revised["source_paragraphs"] = sorted({ref for _, ref in located})
    revised["scene_ids"] = list(dict.fromkeys(new_scenes_by_number[number]["id"] for number, _ in located))
    evidence = "".join(revised["evidence"].split())
    original = "".join("".join(new_paragraphs[ref - 1] for ref in revised["source_paragraphs"]).split())
    if evidence not in original:
        return None
    return revised


def migrate(old_ledger_path, old_source_path, new_script_path, new_workdir, requested):
    old = json.loads(old_ledger_path.read_text(encoding="utf-8-sig"))
    if old.get("schema_version") not in (1, 2):
        raise ValueError("旧台账 schema_version 须为 1 或 2")
    old_paragraphs = source_paragraphs(old_source_path)
    if old.get("source_digest") != digest(old_paragraphs):
        raise ValueError("旧台账与旧原文摘要不匹配")
    new_paragraphs = source_paragraphs(new_workdir / "source.json")
    bases = parse_script(new_script_path)
    ranges = scene_ranges(new_workdir, len(bases), len(new_paragraphs))
    new_signatures = [compact("".join(new_paragraphs[start - 1:end])) for start, end in ranges]
    old_scenes = old["scenes"]
    old_by_id = {scene["id"]: scene for scene in old_scenes}
    lineage, pending = normalize_mapping(requested, old_scenes, new_signatures, old_paragraphs)
    if pending:
        return None, {"status": "需要场景对应", "scene_lineage": lineage, "pending_reviews": pending,
                      "mapping_example": {"scenes": {item["old_id"]: ["填写新场景号；删除填 null"] for item in pending}}}
    incoming, primary, primary_pending = select_primary(lineage, requested, len(bases))
    if primary_pending:
        return None, {"status": "需要主场景 ID", "scene_lineage": lineage,
                      "pending_reviews": primary_pending,
                      "mapping_example": {"primary_ids": {str(item["new_scene"]): "选择一个 old_ids；纯新增场景无需指定" for item in primary_pending}}}

    used = set(old.get("retired_ids", []))
    inactive = copy.deepcopy(old.get("inactive_assets", {"characters": []}))
    if not isinstance(inactive, dict) or not isinstance(inactive.get("characters", []), list):
        raise ValueError("旧台账 inactive_assets 无效")
    inactive.setdefault("characters", [])
    for category in PREFIXES:
        used.update(item["id"] for item in old[category])
    used.update(item["id"] for item in inactive["characters"])
    retired = set(old.get("retired_ids", []))
    retired.update(set(old_by_id) - set(primary.values()))
    pending = [{"kind": "旧台账待核项", "item": item} for item in old.get("pending_reviews", [])]
    characters = copy.deepcopy(old["characters"])
    original_characters = {item["id"]: item for item in old["characters"]}
    locations = copy.deepcopy(old["locations"])
    props = copy.deepcopy(old["props"])
    char_by_id = {item["id"]: item for item in characters}
    loc_by_id = {item["id"]: item for item in locations}
    name_map = requested.get("characters", {})
    if not isinstance(name_map, dict):
        raise ValueError("mapping.characters 必须是旧人物 ID 到新人物行名称的对象")
    for char_id, name in name_map.items():
        dormant = next((item for item in inactive["characters"] if item["id"] == char_id), None)
        if dormant is not None:
            inactive["characters"].remove(dormant)
            characters.append(dormant)
            char_by_id[char_id] = dormant
        if char_id not in char_by_id or not isinstance(name, str) or not name.strip():
            raise ValueError(f"人物改名映射无效：{char_id}")
        item = char_by_id[char_id]
        if item["name"] != name:
            if item["name"] not in item["aliases"]:
                item["aliases"].append(item["name"])
            item["name"] = name

    scenes = []
    for base, source_range in zip(bases, ranges):
        number = base["number"]
        predecessor_ids = incoming[number]
        old_scene_id = primary.get(number)
        scene_id = old_scene_id if old_scene_id else next_id("SC", used)
        prior = [old_by_id[sid] for sid in predecessor_ids]
        matching_locations = {scene["location_id"] for scene in prior
                              if base["location_name"] in [loc_by_id[scene["location_id"]]["name"],
                                                             *loc_by_id[scene["location_id"]]["aliases"]]}
        if len(matching_locations) == 1:
            location_id = next(iter(matching_locations))
        else:
            location_id = next_id("LOC", used)
            item = {"id": location_id, "name": base["location_name"], "aliases": [], "visual_facts": []}
            locations.append(item)
            loc_by_id[location_id] = item
            if prior:
                pending.append({"kind": "地点身份待核", "new_scene": number, "new_location_id": location_id,
                                "old_location_ids": [scene["location_id"] for scene in prior]})
        linked = []
        for name in base["cast_entries"]:
            role_name, _ = split_cast_entry(name)
            options = {ref for scene in prior for ref in scene["character_ids"] if char_by_id[ref]["name"] == name}
            generic = bool(GENERIC_ROLE.match(role_name) or role_name.endswith("-群像"))
            if not options and not generic:
                options = {item["id"] for item in characters if item["name"] == name}
            if len(options) == 1:
                linked.append(next(iter(options)))
            else:
                new_id = next_id("CHAR", used)
                item = {"id": new_id, "name": name, "aliases": [], "visual_facts": [], "personality_facts": []}
                characters.append(item)
                char_by_id[new_id] = item
                linked.append(new_id)
                if options:
                    pending.append({"kind": "同名人物身份待核", "new_scene": number, "new_character_id": new_id,
                                    "old_character_ids": sorted(options)})
        if len(predecessor_ids) == 1 and len(lineage[predecessor_ids[0]]) == 1:
            prop_ids = copy.deepcopy(prior[0]["prop_ids"])
        elif len(predecessor_ids) > 1:
            prop_ids = list(dict.fromkeys(ref for scene in prior for ref in scene["prop_ids"]))
        else:
            prop_ids = []
            if prior and prior[0]["prop_ids"]:
                pending.append({"kind": "拆场道具去向待核", "new_scene": number,
                                "old_prop_ids": prior[0]["prop_ids"]})
        scenes.append({"id": scene_id, "number": number, "heading": base["heading"], "source_range": source_range,
                       "location_id": location_id, "character_ids": linked, "prop_ids": prop_ids,
                       "visual_facts": [], "time_basis": {"kind": "待核", "source_paragraphs": []}})
    new_by_number = {scene["number"]: scene for scene in scenes}
    new_by_id = {scene["id"]: scene for scene in scenes}
    new_index = {}
    for scene in scenes:
        start, end = scene["source_range"]
        index = {}
        for ref in range(start, end + 1):
            index.setdefault(compact(new_paragraphs[ref - 1]), []).append(ref)
        new_index[scene["number"]] = index
    for old_scene in old_scenes:
        if not lineage[old_scene["id"]]:
            pending.append({"kind": "旧场景删除待核", "scene_id": old_scene["id"], "heading": old_scene["heading"],
                            "character_ids": old_scene["character_ids"], "prop_ids": old_scene["prop_ids"],
                            "visual_facts": old_scene["visual_facts"]})
        for fact in old_scene["visual_facts"]:
            revised = migrate_fact(fact, old_by_id, lineage, new_by_number, old_paragraphs, new_paragraphs, new_index)
            if revised and len(revised["scene_ids"]) == 1:
                target = new_by_id[revised["scene_ids"][0]]
                target["visual_facts"].append(revised)
            else:
                pending.append({"kind": "逐场事实证据待核", "old_scene_id": old_scene["id"], "fact": fact})
    for scene in scenes:
        number = scene["number"]
        ids = incoming[number]
        if len(ids) != 1 or len(lineage[ids[0]]) != 1:
            pending.append({"kind": "时间依据待核", "new_scene": number})
            continue
        prior = old_by_id[ids[0]]
        old_time = prior.get("time_basis", {"kind": "待核", "source_paragraphs": []})
        old_mark = re.search(r"：(日|夜)·", prior["heading"])
        new_mark = re.search(r"：(日|夜)·", scene["heading"])
        if old_mark.group(1) != new_mark.group(1):
            pending.append({"kind": "时间依据待核", "new_scene": number, "old_value": old_time})
            continue
        if old_time["kind"] == "格式默认":
            scene["time_basis"] = copy.deepcopy(old_time)
        elif old_time["kind"] == "原文明示":
            relocated = relocate_refs(old_time["source_paragraphs"], [ids[0]], old_by_id, lineage,
                                      new_by_number, old_paragraphs, new_paragraphs, new_index)
            if relocated and all(target == number for target, _ in relocated):
                scene["time_basis"] = {"kind": "原文明示", "source_paragraphs": [ref for _, ref in relocated]}
            else:
                pending.append({"kind": "时间依据待核", "new_scene": number, "old_value": old_time})
        else:
            pending.append({"kind": "时间依据待核", "new_scene": number})

    for category, items in (("characters", characters), ("locations", locations), ("props", props)):
        for item in items:
            for field in ("visual_facts", "personality_facts") if category == "characters" else ("visual_facts",):
                migrated = []
                for fact in item[field]:
                    revised = migrate_fact(fact, old_by_id, lineage, new_by_number, old_paragraphs, new_paragraphs, new_index)
                    field_name = {"characters": "character_ids", "locations": "location_id", "props": "prop_ids"}[category]
                    linked = bool(revised) and all(item["id"] in (
                        [new_by_id[sid][field_name]] if category == "locations" else new_by_id[sid][field_name]
                    ) for sid in revised["scene_ids"])
                    if revised and linked:
                        migrated.append(revised)
                    else:
                        pending.append({"kind": "资产事实证据或出现关系待核", "asset_id": item["id"], "fact": fact})
                item[field] = migrated
    used_characters = {ref for scene in scenes for ref in scene["character_ids"]}
    for item in list(characters):
        if item["id"] not in used_characters:
            characters.remove(item)
            stored = copy.deepcopy(item)
            if item["id"] in original_characters:
                stored["visual_facts"] = copy.deepcopy(original_characters[item["id"]]["visual_facts"])
                stored["personality_facts"] = copy.deepcopy(original_characters[item["id"]]["personality_facts"])
            inactive["characters"].append(stored)
            pending.append({"kind": "人物可能改名或退场", "asset_id": item["id"], "name": item["name"],
                            "action": "确认新人物行身份后恢复关联，或确认退场后移入 retired_ids"})
    result = {"schema_version": 2, "source_digest": digest(new_paragraphs), "scenes": scenes,
              "characters": characters, "locations": locations, "props": props,
              "retired_ids": sorted(retired), "inactive_assets": inactive, "pending_reviews": pending}
    report = {"status": "迁移草稿", "scene_lineage": lineage, "primary_ids": primary,
              "retired_scene_ids": sorted(set(old_by_id) - set(primary.values())),
              "pending_reviews": pending,
              "counts": {"scenes": len(scenes), "characters": len(characters),
                         "locations": len(locations), "props": len(props), "pending": len(pending),
                         "unchanged_source_scenes": sum(
                             old_paragraphs[old_by_id[sid]["source_range"][0] - 1:old_by_id[sid]["source_range"][1]] ==
                             new_paragraphs[new_by_number[numbers[0]]["source_range"][0] - 1:new_by_number[numbers[0]]["source_range"][1]]
                             for sid, numbers in lineage.items() if len(numbers) == 1)}}
    return result, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_ledger", type=Path)
    parser.add_argument("old_source", type=Path)
    parser.add_argument("new_script", type=Path)
    parser.add_argument("new_workdir", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        requested = json.loads(args.mapping.read_text(encoding="utf-8-sig")) if args.mapping else {}
        if not isinstance(requested, dict):
            raise ValueError("mapping 必须是 JSON 对象")
        result, report = migrate(args.old_ledger, args.old_source, args.new_script, args.new_workdir, requested)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if result is None:
            raise ValueError(f"请按迁移报告补充映射：{args.report}")
        if args.output.exists():
            raise ValueError("迁移草稿已存在，请另选输出，避免覆盖人工修改")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已保存迁移草稿：{args.output}；待核 {len(report['pending_reviews'])} 项；报告：{args.report}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
