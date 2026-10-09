#!/usr/bin/env python3
"""Audit source coverage and dialogue attribution before assembling a script."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from asset_ledger import digest, scene_ranges, source_paragraphs, split_cast_entry


SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt$")
TRANSITION = re.compile(r"^(?:遮挡转场|转场)\s*")
REVIEW_VERSION = 3


def compact(value):
    return "".join(char for char in value if char.isalnum())


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scene_files(workdir):
    paths = []
    for path in (workdir / "scenes").glob("*.txt"):
        match = SCENE_FILE.fullmatch(path.name)
        if not match:
            raise ValueError(f"逐场文件名不符合格式：{path.name}")
        paths.append((int(match.group(1)), path))
    paths.sort()
    return paths


def fingerprint_workdir(workdir):
    paragraphs = source_paragraphs(workdir / "source.json")
    paths = scene_files(workdir)
    scene_ranges(workdir, len(paths), len(paragraphs))
    return {"source_digest": digest(paragraphs),
            "drafts": {path.name: sha(path.read_text(encoding="utf-8-sig")) for _, path in paths}}


def cue_reason(value, names):
    squeezed = re.sub(r"\s+", "", value)
    if not squeezed:
        return "空段"
    if squeezed.lower() in ("os", "画外音"):
        return "独立声音提示"
    if squeezed in names:
        return "独立角色名提示"
    if any(squeezed in (name + "OS", name + "os", name + "画外音") for name in names):
        return "独立角色声音提示"
    return None


def draft_parts(path):
    lines = [(i, line.strip()) for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1) if line.strip()]
    if len(lines) < 2 or not lines[0][1].startswith("场景") or not lines[1][1].startswith("人物："):
        raise ValueError(f"场景中间稿缺少标题或人物行：{path.name}")
    cast = lines[1][1]
    if not cast.endswith("；"):
        raise ValueError(f"人物行未以分号结束：{path.name}")
    names = [] if cast == "人物：无；" else [split_cast_entry(part)[0] for part in cast[3:-1].split("；")]
    body, dialogues = [], []
    position = 0
    for number, line in lines[2:]:
        if line.startswith("△ "):
            kind, value, speaker = "action", TRANSITION.sub("", line[2:]), None
        elif "：" in line:
            label, value = line.split("：", 1)
            speaker = re.sub(r"[（(][^（）()]*[）)]$", "", label).strip()
            kind = "dialogue"
            dialogues.append({"line": number, "speaker": speaker, "text": value})
        else:
            raise ValueError(f"{path.name}:{number} 正文行无法识别")
        normalized = compact(value)
        body.append({"line": number, "kind": kind, "text": value, "start": position, "end": position + len(normalized)})
        position += len(normalized)
    return lines[0][1], names, body, dialogues


def quote_blocks(paragraphs, start, end):
    result, opened, pieces = [], None, []
    count = {}
    for i in range(start, end + 1):
        for char in paragraphs[i - 1]:
            if char == "「" and opened is None:
                opened, pieces = i, []
                count[i] = count.get(i, 0) + 1
            elif char == "」" and opened is not None:
                result.append({"paragraph": opened, "end_paragraph": i, "text": "".join(pieces),
                               "ordinal": count[opened], "unclosed": False})
                opened, pieces = None, []
            elif opened is not None:
                pieces.append(char)
    if opened is not None:
        result.append({"paragraph": opened, "end_paragraph": end, "text": "".join(pieces),
                       "ordinal": count[opened], "unclosed": True})
    return result


def cue_hint(paragraphs, start, paragraph, names):
    ordered = sorted(names, key=len, reverse=True)
    for i in range(paragraph, max(start - 1, paragraph - 9), -1):
        value = paragraphs[i - 1].strip()
        if i < paragraph and "」" in value:
            break
        for name in ordered:
            if value.startswith(name):
                suffix = value[len(name):].strip()
                if (not suffix or suffix.lower() in ("os", "画外音") or suffix.startswith(("：", ":", "「"))
                        or re.search(r"说|问|喊|叫|答|开口", suffix.split("「", 1)[0])):
                    return name
    return None


def context(paragraphs, start, end, first, last):
    return ([paragraphs[i - 1].strip() for i in range(max(start, first - 2), first)],
            [paragraphs[i - 1].strip() for i in range(last + 1, min(end, last + 1) + 1)])


def review_key(kind, scene_label, text, before, after, ordinal):
    payload = [kind, re.sub(r"^场景\d+：", "", scene_label), compact(text),
               [compact(x) for x in before], [compact(x) for x in after], ordinal]
    return sha(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))[:24]


def decision_issue(decision, template):
    if decision is None:
        return "待人工确认说话人或引号用途"
    if not isinstance(decision, dict) or any(decision.get(field) != template[field]
                                             for field in ("source_text", "context_before", "context_after", "draft_text")):
        return "旧复核决定已失效，需重新确认"
    return None


def scene_decision_hash(scene, decisions):
    speaker_keys = sorted({item["decision_key"] for item in scene["speaker_items"]})
    paragraph_keys = sorted({item["decision_key"] for item in scene["paragraph_map"] if "decision_key" in item})
    relevant = {"speakers": {key: decisions.get(key) for key in speaker_keys},
                "paragraphs": {key: decisions.get("paragraphs", {}).get(key) for key in paragraph_keys}}
    return sha(json.dumps(relevant, ensure_ascii=False, sort_keys=True))


def check_scene(paragraphs, start, end, path, decisions):
    heading, names, lines, dialogues = draft_parts(path)
    flat = "".join(compact(line["text"]) for line in lines)
    cursor, missing, extra, paragraph_map = 0, [], [], []
    for paragraph in range(start, end + 1):
        value = paragraphs[paragraph - 1].strip()
        reason = cue_reason(value, names)
        if reason:
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "格式提示", "reason": reason, "destinations": []})
            continue
        before, after = context(paragraphs, start, end, paragraph, paragraph)
        disposition_key = review_key("paragraph", heading, value, before, after, 1)
        disposition = decisions.get("paragraphs", {}).get(disposition_key)
        if disposition is not None:
            if (isinstance(disposition, dict) and disposition.get("source_text") == value
                    and disposition.get("context_before") == before and disposition.get("context_after") == after
                    and isinstance(disposition.get("reason"), str) and disposition["reason"].strip()):
                paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "人工确认纯提示",
                                      "reason": disposition["reason"], "decision_key": disposition_key, "destinations": []})
                continue
            missing.append({"paragraph": paragraph, "text": value, "issue": "旧段落去向决定已失效"})
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "待核", "decision_key": disposition_key,
                                  "decision_template": {"source_text": value, "context_before": before, "context_after": after}, "destinations": []})
            continue
        candidate = TRANSITION.sub("", value)
        candidate = re.sub(r"^(?:OS|os|画外音)\s*(?=「)", "", candidate)
        for name in sorted(names, key=len, reverse=True):
            if candidate.startswith((name + "：", name + ":")):
                candidate = candidate[len(name) + 1:]
                break
            if candidate.startswith(name + "「"):
                candidate = candidate[len(name):]
                break
        fragment = compact(candidate)
        if not fragment:
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "纯标点", "reason": "仅含标点", "destinations": []})
            continue
        found = flat.find(fragment, cursor)
        if found < 0 and TRANSITION.match(value) and fragment in compact(heading):
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "转场标题", "reason": "转场词进入场景标题",
                                  "destinations": [{"line": 1, "kind": "heading"}]})
            continue
        if found < 0:
            missing.append({"paragraph": paragraph, "text": value})
            paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "未覆盖", "decision_key": disposition_key,
                                  "decision_template": {"source_text": value, "context_before": before, "context_after": after}, "destinations": []})
            continue
        if found > cursor:
            extra.append({"text": flat[cursor:found][:120], "before_paragraph": paragraph,
                          "draft_lines": [line["line"] for line in lines if line["start"] < found and line["end"] > cursor]})
        finish = found + len(fragment)
        destinations = [{"line": line["line"], "kind": line["kind"]} for line in lines
                        if line["start"] < finish and line["end"] > found]
        paragraph_map.append({"paragraph": paragraph, "source_text": value, "status": "已覆盖", "destinations": destinations})
        cursor = finish
    if cursor < len(flat):
        extra.append({"text": flat[cursor:][:120], "after_paragraph": end,
                      "draft_lines": [line["line"] for line in lines if line["end"] > cursor]})

    quote_items, assigned_lines = [], set()
    for block in quote_blocks(paragraphs, start, end):
        before, after = context(paragraphs, start, end, block["paragraph"], block["end_paragraph"])
        key = review_key("quote", heading, block["text"], before, after, block["ordinal"])
        source_lines = {dest["line"] for item in paragraph_map
                        if block["paragraph"] <= item["paragraph"] <= block["end_paragraph"]
                        for dest in item["destinations"] if dest["kind"] == "dialogue"}
        matching = [d for d in dialogues if d["line"] in source_lines and d["line"] not in assigned_lines
                    and compact(block["text"]) == compact(d["text"])]
        dialogue = matching[0] if matching else None
        template = {"source_text": block["text"], "context_before": before, "context_after": after,
                    "draft_text": dialogue["text"] if dialogue else ""}
        decision = decisions.get(key)
        issues = []
        if block["unclosed"]:
            issues.append("原文引号未闭合")
        invalid = decision_issue(decision, template) if decision is not None else None
        if invalid:
            issues.append(invalid)
            decision = None
        if decision and decision.get("classification") == "非台词":
            if dialogue:
                issues.append("引号已匹配成稿台词，不能标为非台词")
        elif dialogue:
            assigned_lines.add(dialogue["line"])
            hint = cue_hint(paragraphs, start, block["paragraph"], names)
            cast_match = all(part in names for part in dialogue["speaker"].split("、"))
            if not cast_match:
                issues.append("说话人未逐字列入人物行")
            if (not hint or hint not in dialogue["speaker"].split("、")) and not (decision and decision.get("speaker") == dialogue["speaker"]):
                issues.append("待人工确认说话人")
        else:
            issues.append("原文引号未匹配成稿台词；若非台词须人工确认")
        quote_items.append({"kind": "原文引号", "paragraph": block["paragraph"], "end_paragraph": block["end_paragraph"],
                            "decision_key": key, "source_text": block["text"], "draft_line": dialogue["line"] if dialogue else None,
                            "speaker": dialogue["speaker"] if dialogue else None, "cue_hint": cue_hint(paragraphs, start, block["paragraph"], names),
                            "issue": issues, "decision_template": template})

    for dialogue in dialogues:
        if dialogue["line"] in assigned_lines:
            continue
        refs = [item["paragraph"] for item in paragraph_map if any(dest["line"] == dialogue["line"] for dest in item["destinations"])]
        source_text = "".join(paragraphs[ref - 1] for ref in refs)
        first, last = (min(refs), max(refs)) if refs else (start, start)
        before, after = context(paragraphs, start, end, first, last)
        ordinal = sum(1 for item in dialogues if item["line"] <= dialogue["line"] and compact(item["text"]) == compact(dialogue["text"]))
        key = review_key("unquoted", heading, source_text + dialogue["text"], before, after, ordinal)
        template = {"source_text": source_text, "context_before": before, "context_after": after, "draft_text": dialogue["text"]}
        decision = decisions.get(key)
        issues = []
        invalid = decision_issue(decision, template) if decision is not None else None
        if invalid:
            issues.append(invalid)
            decision = None
        if not refs or compact(dialogue["text"]) not in compact(source_text):
            issues.append("无引号台词未能对应原文文字")
        if any(part not in names for part in dialogue["speaker"].split("、")):
            issues.append("说话人未逐字列入人物行")
        hint = cue_hint(paragraphs, start, first, names)
        if (not hint or hint not in dialogue["speaker"].split("、")) and not (decision and decision.get("speaker") == dialogue["speaker"]):
            issues.append("无引号台词说话人待核")
        quote_items.append({"kind": "无引号台词", "paragraph": first, "source_paragraphs": refs,
                            "decision_key": key, "source_text": source_text, "draft_line": dialogue["line"],
                            "speaker": dialogue["speaker"], "cue_hint": hint, "issue": issues,
                            "decision_template": template})
    result = {"scene": heading, "file": path.name, "source_range": [start, end],
            "source_hash": sha(json.dumps(paragraphs[start - 1:end], ensure_ascii=False)),
            "draft_hash": sha(path.read_text(encoding="utf-8-sig")), "paragraph_map": paragraph_map,
            "coverage_missing": missing, "coverage_extra": extra, "speaker_items": quote_items}
    result["decision_hash"] = scene_decision_hash(result, decisions)
    return result


def shift_cached(scene, new_start, file_name):
    result = copy.deepcopy(scene)
    delta = new_start - result["source_range"][0]
    result["source_range"] = [value + delta for value in result["source_range"]]
    result["file"] = file_name
    for item in result["paragraph_map"]:
        item["paragraph"] += delta
    for item in result["coverage_missing"]:
        item["paragraph"] += delta
    for item in result["coverage_extra"]:
        for key in ("before_paragraph", "after_paragraph"):
            if key in item:
                item[key] += delta
    for item in result["speaker_items"]:
        item["paragraph"] += delta
        if "end_paragraph" in item:
            item["end_paragraph"] += delta
        if "source_paragraphs" in item:
            item["source_paragraphs"] = [value + delta for value in item["source_paragraphs"]]
    return result


def make_report(workdir, decisions, previous=None):
    if not isinstance(decisions, dict) or not isinstance(decisions.get("paragraphs", {}), dict):
        raise ValueError("确认文件须为 JSON 对象；paragraphs 也须为对象")
    paragraphs = source_paragraphs(workdir / "source.json")
    paths = scene_files(workdir)
    ranges = scene_ranges(workdir, len(paths), len(paragraphs))
    decision_digest = sha(json.dumps(decisions, ensure_ascii=False, sort_keys=True))
    prior = {}
    if previous and previous.get("review_version") == REVIEW_VERSION:
        for item in previous.get("scenes", []):
            prior.setdefault((item["source_hash"], item["draft_hash"]), []).append(item)
    scenes, reused = [], 0
    for (_, path), (start, end) in zip(paths, ranges):
        source_hash = sha(json.dumps(paragraphs[start - 1:end], ensure_ascii=False))
        draft_hash = sha(path.read_text(encoding="utf-8-sig"))
        candidates = [item for item in prior.get((source_hash, draft_hash), [])
                      if item.get("decision_hash") == scene_decision_hash(item, decisions)]
        if len(candidates) == 1:
            scenes.append(shift_cached(candidates[0], start, path.name))
            reused += 1
        else:
            scenes.append(check_scene(paragraphs, start, end, path, decisions))
    missing = sum(len(item["coverage_missing"]) for item in scenes)
    extra = sum(len(item["coverage_extra"]) for item in scenes)
    speaker = sum(bool(part["issue"]) for item in scenes for part in item["speaker_items"])
    return {"review_version": REVIEW_VERSION, "status": "passed" if not (missing or extra or speaker) else "needs_review",
            "fingerprint": fingerprint_workdir(workdir), "decision_digest": decision_digest,
            "counts": {"missing": missing, "extra": extra, "speaker": speaker, "reused_scenes": reused}, "scenes": scenes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--previous-report", type=Path)
    args = parser.parse_args()
    try:
        decisions = json.loads(args.decisions.read_text(encoding="utf-8-sig")) if args.decisions else {}
        if not isinstance(decisions, dict):
            raise ValueError("说话人确认文件须为 decision_key 到确认对象的 JSON 对象")
        previous = json.loads(args.previous_report.read_text(encoding="utf-8-sig")) if args.previous_report else None
        report = make_report(args.workdir, decisions, previous)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        counts = report["counts"]
        print(f"逐场复核：漏文 {counts['missing']}，额外文字 {counts['extra']}，台词待处理 {counts['speaker']}，复用未改场景 {counts['reused_scenes']}；报告：{args.report}")
        if report["status"] != "passed":
            raise ValueError("逐场复核未通过，请修正逐场稿或补全有效的人工确认")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
