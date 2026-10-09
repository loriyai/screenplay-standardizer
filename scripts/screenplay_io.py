#!/usr/bin/env python3
"""Prepare numbered source paragraphs and assemble checked scene drafts."""

import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SCENE_FILE = re.compile(r"scene_(\d+)_(\d+)-(\d+)\.txt$")
HEADING = re.compile(r"场景(\d+)：(日|夜)·(内|外)·\S+$")
DIALOGUE = re.compile(r"[^：:\s△]{1,30}(?:\([^()]+\)|（[^（）]+）)?：\S.*$")


def read_source(path: Path) -> list[str]:
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8-sig").splitlines()
    if path.suffix.lower() != ".docx":
        raise ValueError("输入须为 UTF-8 TXT 或 DOCX")
    with zipfile.ZipFile(path) as doc:
        root = ET.fromstring(doc.read("word/document.xml"))
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("DOCX 中没有正文")
    paragraphs = []
    for paragraph in body.iter(f"{W}p"):
        parts = []
        for item in paragraph.iter():
            if item.tag == f"{W}t":
                parts.append(item.text or "")
            elif item.tag == f"{W}tab":
                parts.append("\t")
            elif item.tag in {f"{W}br", f"{W}cr"}:
                parts.append("\n")
        paragraphs.append("".join(parts))
    return paragraphs


def prepare(input_path: Path, workdir: Path) -> None:
    if not input_path.is_file():
        raise ValueError(f"找不到输入文件：{input_path}")
    source_file = workdir / "source.json"
    scene_dir = workdir / "scenes"
    if source_file.exists() or (scene_dir.exists() and any(scene_dir.iterdir())):
        raise ValueError(f"工作目录已有原文或逐场中间稿，避免覆盖：{workdir}")
    paragraphs = read_source(input_path)
    if not paragraphs:
        raise ValueError("输入文件没有可编号的段落")
    workdir.mkdir(parents=True, exist_ok=True)
    scene_dir.mkdir(exist_ok=True)
    payload = {
        "input": str(input_path.resolve()),
        "paragraphs": [{"id": i, "text": value} for i, value in enumerate(paragraphs, 1)],
    }
    source_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    width = max(4, len(str(len(paragraphs))))
    numbered = "\n".join(f"[{i:0{width}d}] {value}" for i, value in enumerate(paragraphs, 1)) + "\n"
    (workdir / "source_numbered.txt").write_text(numbered, encoding="utf-8")
    print(f"已提取 {len(paragraphs)} 段；逐场中间稿目录：{scene_dir}")


def validate_scene(content: str, number: int, path: Path) -> list[str]:
    errors = []
    lines = [(i, line.strip()) for i, line in enumerate(content.splitlines(), 1) if line.strip()]
    if len(lines) < 2:
        return [f"{path.name}：缺少场景标题或人物行"]
    if not (match := HEADING.fullmatch(lines[0][1])) or int(match.group(1)) != number:
        errors.append(f"{path.name}:{lines[0][0]}：场景标题应为 场景{number}：日/夜·内/外·地点")
    cast = lines[1][1]
    if not cast.startswith("人物："):
        errors.append(f"{path.name}:{lines[1][0]}：第二行必须以 人物： 开头")
    elif not cast.endswith("；") or not cast[3:-1]:
        errors.append(f"{path.name}:{lines[1][0]}：人物行须有条目且以 ； 结尾")
    elif cast != "人物：无；":
        entries = cast[3:-1].split("；")
        if any(not entry or "-" not in entry or not all(part.strip() for part in entry.split("-")) for entry in entries):
            errors.append(f"{path.name}:{lines[1][0]}：人物条目应为 角色名-形象；")
    for line_no, line in lines[2:]:
        if not line.startswith("△ ") and not DIALOGUE.fullmatch(line):
            errors.append(f"{path.name}:{line_no}：正文应为 △ 动作段 或 角色名：台词")
    return errors


def assemble(workdir: Path, output: Path, review_report: Path) -> None:
    from screenplay_review import REVIEW_VERSION, fingerprint_workdir

    report = json.loads(review_report.read_text(encoding="utf-8-sig"))
    if report.get("review_version") != REVIEW_VERSION or report.get("status") != "passed":
        raise ValueError("合并前须提供当前版本且已通过的逐场复核报告")
    if report.get("fingerprint") != fingerprint_workdir(workdir):
        raise ValueError("复核报告与当前原文或逐场稿不一致，请重新复核")
    counts = report.get("counts", {})
    if any(counts.get(key) != 0 for key in ("missing", "extra", "speaker")):
        raise ValueError("复核报告仍有未处理项目")
    source_file = workdir / "source.json"
    if not source_file.is_file():
        raise ValueError(f"找不到 {source_file}；请先运行 prepare")
    source = json.loads(source_file.read_text(encoding="utf-8"))
    paragraphs = source["paragraphs"]
    drafts = []
    for path in (workdir / "scenes").glob("*.txt"):
        match = SCENE_FILE.fullmatch(path.name)
        if not match:
            raise ValueError(f"中间稿文件名不符合 scene_001_0001-0049.txt：{path.name}")
        drafts.append((*(int(x) for x in match.groups()), path))
    drafts.sort(key=lambda item: item[0])
    if not drafts:
        raise ValueError("没有逐场中间稿")
    errors = []
    expected_paragraph = 1
    assembled = []
    for expected_scene, (scene_no, start, end, path) in enumerate(drafts, 1):
        if scene_no != expected_scene:
            errors.append(f"场景编号缺失或重复：应为 {expected_scene}，实际为 {scene_no}")
        if start != expected_paragraph or end < start:
            errors.append(f"{path.name}：段落范围应从 {expected_paragraph} 开始且终点不早于起点")
        expected_paragraph = end + 1
        content = path.read_text(encoding="utf-8-sig").strip()
        errors.extend(validate_scene(content, scene_no, path))
        assembled.append(content)
    if expected_paragraph != len(paragraphs) + 1:
        errors.append(f"段落编号须覆盖 1–{len(paragraphs)}；最后覆盖至 {expected_paragraph - 1}")
    if errors:
        raise ValueError("合并前检查未通过：\n" + "\n".join(errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=output.parent, delete=False) as temp:
        temp.write("\n\n".join(assembled) + "\n")
        temp_path = Path(temp.name)
    try:
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    print(f"已合并 {len(drafts)} 场、覆盖 {len(paragraphs)} 段：{output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="提取原文并生成内部段落编号")
    prep.add_argument("input", type=Path)
    prep.add_argument("--workdir", type=Path, required=True)
    join = sub.add_parser("assemble", help="验证逐场中间稿并合并")
    join.add_argument("workdir", type=Path)
    join.add_argument("--output", type=Path, required=True)
    join.add_argument("--review-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.input, args.workdir)
        else:
            assemble(args.workdir, args.output, args.review_report)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
