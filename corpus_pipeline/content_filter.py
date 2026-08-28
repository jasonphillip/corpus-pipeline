"""Content filtering for corpus acquisition and chunking — language detection, quality gates, boilerplate removal."""

import re


# CJK Unicode ranges (Chinese, Japanese, Korean)
_CJK_RE = re.compile(
    r'[\u2E80-\u2FFF'   # CJK Radicals
    r'\u3000-\u303F'     # CJK Symbols
    r'\u3040-\u309F'     # Hiragana
    r'\u30A0-\u30FF'     # Katakana
    r'\u3400-\u4DBF'     # CJK Unified Ext A
    r'\u4E00-\u9FFF'     # CJK Unified
    r'\uF900-\uFAFF'     # CJK Compat
    r'\U00020000-\U0002A6DF'  # CJK Ext B
    r']'
)

# Patterns indicating full-document noise (reject entire content)
_NOISE_PATTERNS = [
    re.compile(r'<\?xml\s', re.IGNORECASE),
    re.compile(r'<urlset\b', re.IGNORECASE),
    re.compile(r'<sitemapindex\b', re.IGNORECASE),
    re.compile(r'^User-agent:\s*\*', re.MULTILINE | re.IGNORECASE),
    re.compile(r'^\s*Disallow:\s*/', re.MULTILINE),
]

# Boilerplate line patterns — matched line-by-line and stripped from chunks
_BOILERPLATE_LINE_PATTERNS = [
    # Cookie/consent banners
    re.compile(r'(?:accept\s+(?:all\s+)?cookies?|cookie\s+(?:policy|consent|preferences|settings|notice))', re.IGNORECASE),
    re.compile(r'(?:we\s+use\s+cookies|this\s+(?:site|website)\s+uses?\s+cookies)', re.IGNORECASE),
    re.compile(r'(?:manage\s+(?:cookie|consent)\s+preferences)', re.IGNORECASE),
    # Privacy/legal footers
    re.compile(r'(?:privacy\s+(?:policy|notice|statement)|terms\s+(?:of\s+(?:service|use))|legal\s+notice)', re.IGNORECASE),
    re.compile(r'(?:all\s+rights?\s+reserved|©\s*\d{4})', re.IGNORECASE),
    # Navigation fragments
    re.compile(r'(?:skip\s+to\s+(?:main\s+)?content|back\s+to\s+top|table\s+of\s+contents)', re.IGNORECASE),
    re.compile(r'(?:previous\s+page|next\s+page|previous\s+article|next\s+article)\s*[|»›]?$', re.IGNORECASE),
    re.compile(r'^\s*(?:home|about|contact|blog|docs|support|pricing|sign\s*(?:in|up))\s*[|/»›]\s*(?:home|about|contact|blog|docs|support|pricing|sign\s*(?:in|up))', re.IGNORECASE),
    # Tracking/analytics
    re.compile(r'(?:bizible\.com|onetrust|googletagmanager|google-analytics|facebook\.net/|doubleclick\.net)', re.IGNORECASE),
    re.compile(r'(?:gtag\s*\(|_gaq\.push|fbq\s*\(|analytics\.js)', re.IGNORECASE),
    # Powered-by / platform footers
    re.compile(r'(?:powered\s+by|built\s+with|made\s+with)\s+\w+', re.IGNORECASE),
    re.compile(r'(?:hosted\s+(?:on|by)|deployed\s+(?:on|with))\s+\w+', re.IGNORECASE),
    # Social sharing
    re.compile(r'(?:share\s+(?:on|this|via)\s+(?:twitter|facebook|linkedin|reddit|x\.com))', re.IGNORECASE),
    re.compile(r'(?:follow\s+us\s+on|join\s+(?:our|the)\s+community)', re.IGNORECASE),
    # Newsletter/subscribe
    re.compile(r'(?:subscribe\s+to\s+(?:our|the)\s+newsletter|enter\s+your\s+email)', re.IGNORECASE),
]

# Boilerplate block patterns — multi-line sections to remove entirely
_BOILERPLATE_BLOCK_PATTERNS = [
    # Script/style blocks that survived HTML→markdown conversion
    re.compile(r'<script\b[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL),
    re.compile(r'<style\b[^>]*>.*?</style>', re.IGNORECASE | re.DOTALL),
    re.compile(r'<noscript\b[^>]*>.*?</noscript>', re.IGNORECASE | re.DOTALL),
    # Inline CSS/JS artifacts
    re.compile(r'\{[^}]*(?:display\s*:|font-size\s*:|margin\s*:|padding\s*:)[^}]*\}', re.IGNORECASE),
]

# HTML/XML tag pattern
_TAG_RE = re.compile(r'<[^>]+>')

# URL/image-heavy line detection
_URL_RE = re.compile(r'https?://\S+')
_IMG_RE = re.compile(r'!\[[^\]]*\]\([^)]+\)')


def is_target_language(text: str, cjk_threshold: float = 0.3) -> bool:
    """Check if text is primarily in a Latin-script language (English, etc.).

    Returns False if CJK characters exceed the threshold ratio of all alphanumeric chars.
    This is a lightweight heuristic — not a full language detector.
    """
    if not text:
        return True

    # Sample first 2000 chars for performance
    sample = text[:2000]
    cjk_count = len(_CJK_RE.findall(sample))
    if cjk_count == 0:
        return True

    # Count all alphanumeric + CJK chars
    alpha_count = sum(1 for c in sample if c.isalnum())
    if alpha_count == 0:
        return True

    return (cjk_count / alpha_count) < cjk_threshold


def is_valid_content(text: str, min_text_chars: int = 200) -> bool:
    """Check if text has enough actual content to be worth indexing.

    Rejects:
    - Files that are mostly XML/HTML tags (tag ratio > 50%)
    - Files with too little text content after stripping markup
    - Files matching known noise patterns (sitemaps, robots.txt)
    """
    if not text or not text.strip():
        return False

    stripped = text.strip()

    # Check for noise patterns
    for pattern in _NOISE_PATTERNS:
        if pattern.search(stripped[:1000]):
            return False

    # Check tag ratio — if more than 50% of content is tags, it's markup not docs
    tags = _TAG_RE.findall(stripped)
    tag_chars = sum(len(t) for t in tags)
    if len(stripped) > 0 and (tag_chars / len(stripped)) > 0.5:
        return False

    # Check remaining text after stripping tags
    clean = _TAG_RE.sub('', stripped).strip()
    if len(clean) < min_text_chars:
        return False

    return True


def strip_boilerplate(text: str) -> str:
    """Remove boilerplate sections from chunk text while preserving good content.

    Works at both the block level (script/style tags) and line level
    (cookie banners, nav fragments, tracking pixels, footers).
    """
    if not text:
        return text

    # Remove boilerplate blocks first
    result = text
    for pattern in _BOILERPLATE_BLOCK_PATTERNS:
        result = pattern.sub('', result)

    # Remove boilerplate lines
    lines = result.split('\n')
    clean_lines = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            clean_lines.append(line)
            continue
        is_boilerplate = False
        for pattern in _BOILERPLATE_LINE_PATTERNS:
            if pattern.search(stripped_line):
                is_boilerplate = True
                break
        if not is_boilerplate:
            clean_lines.append(line)

    # Collapse runs of 3+ blank lines to 2
    result = '\n'.join(clean_lines)
    result = re.sub(r'\n{4,}', '\n\n\n', result)
    return result.strip()


def information_density(text: str) -> float:
    """Score the information density of a chunk from 0.0 to 1.0.

    Low density = mostly URLs, image markdown, whitespace, or tags.
    High density = mostly readable prose or code.
    Returns the ratio of 'real text' characters to total characters.
    """
    if not text or not text.strip():
        return 0.0

    total = len(text)

    # Remove URLs, image tags, HTML tags, and excessive whitespace
    cleaned = _URL_RE.sub('', text)
    cleaned = _IMG_RE.sub('', cleaned)
    cleaned = _TAG_RE.sub('', cleaned)

    # Count remaining non-whitespace characters
    real_chars = sum(1 for c in cleaned if not c.isspace())
    return real_chars / total if total > 0 else 0.0


def clean_chunk(text: str, min_density: float = 0.3) -> str | None:
    """Clean a chunk by stripping boilerplate, then check information density.

    Returns cleaned text, or None if the chunk is too low-quality to keep.
    """
    cleaned = strip_boilerplate(text)
    if not cleaned or len(cleaned.strip()) < 50:
        return None
    if information_density(cleaned) < min_density:
        return None
    return cleaned

