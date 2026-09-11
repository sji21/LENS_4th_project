"""Conservative paragraph existence checks on the supplied evidence only.

No database offsets or other versions are consulted. Ambiguous excerpts are
unverifiable, not proof that a paragraph does not exist in the law.
"""
import re
import unicodedata

from src.generation.citation import _law_mentions


def claim_identity(text, article_span):
    for match, law, article in _law_mentions(text):
        if match.span('article') == article_span:
            return law, article
    return None


def _without_quotes(text):
    # Preserve line boundaries; quoted/fenced paragraphs are never structural.
    text = re.sub(r'```[\s\S]*?(?:```|\Z)', '', text)
    pairs = {'"': '"', "'": "'", '“': '”', '‘': '’', '「': '」', '『': '』', '`': '`'}
    stack, result = [], []
    for char in text:
        if stack and char == stack[-1]:
            stack.pop()
            result.append(' ')
        elif char in pairs:
            stack.append(pairs[char])
            result.append(' ')
        else:
            result.append(char if not stack or char == '\n' else ' ')
    return '\n'.join('' if line.lstrip().startswith('>') else line
                     for line in ''.join(result).splitlines())


def evidence_paragraphs(evidence, identity):
    mentions = _law_mentions(evidence.citation)
    if identity is None or len(mentions) != 1 or mentions[0][1:] != identity:
        return set()
    text = evidence.text.strip()
    # A leading retrieval header may share its line with paragraph 1.
    if text.startswith('['):
        header = re.match(r'\[([^\]\n]+)\]', text)
        if header is None:
            return set()
        header_mentions = _law_mentions(header[1])
        if len(header_mentions) != 1 or header_mentions[0][1:] != identity:
            return set()
        text = text[header.end():].lstrip()
    text = _without_quotes(text)
    numbers = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Additional article headings make the excerpt's ownership ambiguous.
        if line.startswith('[') or re.match(r'제\s*\d+\s*조', line) or (
                _law_mentions(line) and _law_mentions(line)[0][0].start() == 0):
            return set()
        marker = re.match(r'([①-⑳㉑-㉟㊱-㊿])', line)
        if marker:
            numbers.append(int(unicodedata.numeric(marker[1])))
        elif not numbers:
            # Do not interpret prose introductions, quoted extracts, or plain
            # unnumbered text as an implicit first paragraph.
            return set()
    if numbers != list(range(1, len(numbers) + 1)):
        return set()
    return set(numbers)
