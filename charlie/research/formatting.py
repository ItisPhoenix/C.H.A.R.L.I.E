import re

_CODE_REQUEST_RE = re.compile(
    r"\b(?:code|snippet|implementation|implement|source\s+file|file\s+path|which\s+files?)\b",
    re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(r"(?ms)^```[^\n]*\n.*?^```\s*$\n?")
_CODE_SAMPLE_LINE_RE = re.compile(
    r"^\s*(?:import\s+(?:\{|[\w.*]+\s+from\b)|from\s+[\w.]+\s+import\b|"
    r"(?:const|let|var|export)\s+\w+\s*=|@/[^\s]+|[\w./-]+\.module\.css\b)",
    re.IGNORECASE,
)


def format_research_answer(answer: str, query: str) -> str:
    """Keep research prose readable without hiding code the user requested."""
    if not answer or _CODE_REQUEST_RE.search(query):
        return answer
    without_fences = _FENCED_CODE_RE.sub("", answer)
    blocks = []
    for block in re.split(r"\n\s*\n", without_fences):
        lines = [line for line in block.splitlines() if not _CODE_SAMPLE_LINE_RE.match(line)]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            blocks.append(cleaned)
    return "\n\n".join(blocks) or answer


def compact_quick_search_answer(answer: str) -> str:
    """Render quick lookup output as one short cited paragraph, never page excerpts."""
    lines = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(
            r"^(?:#{1,6}\s*)?(?:sources?|coverage gaps|gaps and notes|what could not be confirmed)\b",
            line,
            re.IGNORECASE,
        ):
            break
        if re.match(r"^#{1,6}\s+", line):
            continue
        line = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)

    sentences = []
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
            sentence = sentence.strip()
            if sentence:
                sentences.append(sentence)
            if len(sentences) == 2:
                break
        if len(sentences) == 2:
            break
    if not sentences:
        return ""
    citation_ids = list(dict.fromkeys(re.findall(r"\[S\d+\]", sentences[0])))
    sentences[0] = re.sub(r"\s*\[S\d+\]", "", sentences[0]).strip()
    if citation_ids:
        sentences[0] += " " + " ".join(citation_ids)
    return " ".join(sentences[:2])
