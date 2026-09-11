"""Conservative paragraph existence checks on the supplied evidence only.

No database offsets or other versions are consulted. Ambiguous excerpts are
unverifiable, not proof that a paragraph does not exist in the law.
"""
import re
import unicodedata

from src.generation.citation import _law_mentions

_CONTINUATION = re.compile(
    r'\s*(?P<join>부터|내지|[~～–—-]|[·ㆍ,](?:\s*및)?|및|와|과|또는)'
    r'\s*(?:제\s*)?(?P<number>\d+)\s*항(?:\s*까지)?')
_ARTICLE = r'제\s*\d+\s*조(?:\s*의\s*\d+)?'
_HISTORY = re.compile(
    r'\[(?:(?:전문개정|본조신설|제목개정|제목변경)\s*'
    r'|'+_ARTICLE+r'에서\s*이동(?:,\s*종전\s*'+_ARTICLE+r'는\s*'+_ARTICLE+r'로\s*이동)?\s*'
    r'|종전\s*'+_ARTICLE+r'는\s*'+_ARTICLE+r'로\s*이동\s*)'
    r'<?\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?>?\]')


def paragraph_group(text, match):
    """Read only contiguous list/range suffixes owned by this article mention."""
    previous = int(match.group('paragraph'))
    numbers = {previous}
    end = match.end()
    while tail := _CONTINUATION.match(text, end):
        number = int(tail['number'])
        if tail['join'] in ('부터', '내지', '~', '～', '–', '—', '-'):
            # Evidence markers currently support 1..50. Refuse invalid or huge
            # ranges without allocating attacker-controlled ranges.
            if not 1 <= previous <= number <= 50:
                numbers.add(0)
            else:
                numbers.update(range(previous, number + 1))
        else:
            numbers.add(number)
        previous, end = number, tail.end()
    return numbers, text[match.start():end]


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
        if _HISTORY.fullmatch(line):
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
