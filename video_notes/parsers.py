"""Parse text tracks without saving subtitle files or running executable content."""
import html
import json
import math
import re
import xml.etree.ElementTree as ET


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", str(text))).strip()


def seconds(value):
    parts = value.replace(",", ".").split(":")
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(parts)))


def parse_subtitles(text, ext):
    text = text.lstrip("\ufeff").strip()
    rows = []
    if ext in {"json", "json3"} or text.startswith("{"):
        data = json.loads(text)
        if "body" in data:  # Bilibili JSON
            rows = [(r["from"], r["to"], r["content"]) for r in data["body"]]
        elif "events" in data:  # YouTube JSON3
            for e in data["events"]:
                if not e.get("segs"):
                    continue
                start = e["tStartMs"] / 1000
                rows.append((start, start + e.get("dDurationMs", 0) / 1000,
                             "".join(s.get("utf8", "") for s in e["segs"])))
        else:
            raise ValueError("Unrecognized subtitle JSON")
    elif ext in {"srv1", "srv2", "srv3", "ttml"}:
        root = ET.fromstring(text)
        for e in root.iter():
            tag = e.tag.split("}")[-1]
            if tag == "text" and "start" in e.attrib:
                start = float(e.attrib["start"])
                rows.append((start, start + float(e.get("dur", 0)), "".join(e.itertext())))
            elif tag == "p" and "t" in e.attrib:
                start = float(e.attrib["t"]) / 1000
                rows.append((start, start + float(e.get("d", 0)) / 1000, "".join(e.itertext())))
            elif tag == "p" and "begin" in e.attrib and "end" in e.attrib:
                def time_value(v):
                    return float(v[:-1]) if v.endswith("s") else seconds(v)
                rows.append((time_value(e.attrib["begin"]), time_value(e.attrib["end"]), "".join(e.itertext())))
    elif ext in {"vtt", "srt"}:
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
            if block.startswith(("NOTE", "STYLE", "REGION")):
                continue
            lines = block.splitlines()
            for i, line in enumerate(lines):
                match = re.match(r"\s*([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
                if match:
                    rows.append((seconds(match[1]), seconds(match[2]), "\n".join(lines[i+1:])))
                    break
    else:
        raise ValueError(f"Unsupported subtitle format: {ext}")
    result = []
    seen = set()
    for start, end, content in rows:
        start, end, content = float(start), float(end), clean(content)
        if not content:
            continue
        if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end < start:
            raise ValueError("Invalid subtitle timestamp")
        key = (start, end, content)
        if key not in seen:
            seen.add(key)
            result.append(dict(start=start, end=end, text=content))
    if not result:
        raise ValueError("No non-empty subtitle cues")
    return sorted(result, key=lambda x: (x["start"], x["end"]))
