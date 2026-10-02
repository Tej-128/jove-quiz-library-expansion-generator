from __future__ import annotations

import re

# Standard inline/display LaTeX delimiters that may already exist in source/model output.
_LATEX_SPAN_RE = re.compile(r"(\\\(.+?\\\)|\\\[.+?\\\]|\$[^$]+\$)", re.DOTALL)

_OPERATOR_MAP = {
    "×": r"\times",
    "÷": r"\div",
    "·": r"\cdot",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "≈": r"\approx",
    "±": r"\pm",
    "→": r"\rightarrow",
    "↔": r"\leftrightarrow",
    "⇌": r"\rightleftharpoons",
    "∞": r"\infty",
    "π": r"\pi",
    "Δ": r"\Delta",
    "δ": r"\delta",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "λ": r"\lambda",
    "μ": r"\mu",
    "σ": r"\sigma",
    "θ": r"\theta",
}

_SUPERSCRIPTS = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-",
})
_SUBSCRIPTS = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "₊": "+", "₋": "-",
})

# Obvious equation relations. We intentionally do not treat ordinary slashes or hyphens
# as equations because that would alter normal prose/options.
_RELATION_RE = re.compile(r"(?<!\\)(=|≤|≥|≠|≈|→|↔|⇌)")


def _replace_unicode_scripts(expr: str) -> str:
    # Convert runs of unicode superscripts/subscripts to explicit LaTeX groups.
    def sup_repl(match: re.Match[str]) -> str:
        return "^{" + match.group(0).translate(_SUPERSCRIPTS) + "}"

    def sub_repl(match: re.Match[str]) -> str:
        return "_{" + match.group(0).translate(_SUBSCRIPTS) + "}"

    expr = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", sup_repl, expr)
    expr = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋]+", sub_repl, expr)
    return expr


def _latexify_expression(expr: str) -> str:
    expr = re.sub(r"\s+", " ", str(expr or "")).strip()
    if not expr:
        return expr

    expr = _replace_unicode_scripts(expr)
    for raw, latex in _OPERATOR_MAP.items():
        expr = expr.replace(raw, latex)

    # Common square-root forms. Keep the transformation deliberately narrow.
    expr = re.sub(r"√\s*\(([^()]+)\)", r"\\sqrt{\1}", expr)
    expr = re.sub(r"√\s*([A-Za-z0-9]+)", r"\\sqrt{\1}", expr)

    # Make common chemical formula digits explicit subscripts without touching leading
    # stoichiometric coefficients: H2O -> H_{2}O, CO2 -> CO_{2}, 2H2 -> 2H_{2}.
    expr = re.sub(r"([A-Z][a-z]?)(\d+)", r"\1_{\2}", expr)

    # Escape percent signs inside math mode unless already escaped.
    expr = re.sub(r"(?<!\\)%", r"\\%", expr)

    return r"\(" + expr + r"\)"


def _lhs_start(text: str, relation_start: int) -> int:
    """Find a conservative start for the left side of an inline equation."""
    prefix = text[:relation_start]
    boundary = max(prefix.rfind(ch) for ch in ".,;:?!|\n") + 1
    candidate = prefix[boundary:]

    # Drop common prose cues so "Using ROI = ..." becomes "ROI = ...".
    cue = re.search(r"\b(?:using|given|where|when|if|is|calculate|determine|formula|equation|reaction\s+is|formula\s+is|equation\s+is)\s+(.+)$", candidate, re.I)
    if cue:
        return boundary + cue.start(1)

    # Otherwise take the last compact identifier/title-like phrase.
    m = re.search(r"([A-Za-z][A-Za-z0-9_]*(?:\s*\([A-Za-z0-9_]+\))?|[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,3})\s*$", candidate)
    if m:
        return boundary + m.start(1)
    return boundary


def _rhs_end(text: str, relation_end: int) -> int:
    """Find a conservative end for the right side of an inline equation."""
    tail = text[relation_end:]
    # Commas/semicolons/question marks/exclamation marks are strong boundaries.
    stops = [idx for idx in (tail.find(","), tail.find(";"), tail.find("?"), tail.find("!"), tail.find("|")) if idx >= 0]
    word_stop = re.search(r"\s+\b(?:where|when|if|then|what|which|who|how|calculate|determine|correct|true|false)\b", tail, re.I)
    if word_stop:
        stops.append(word_stop.start())
    end = relation_end + (min(stops) if stops else len(tail))

    # If we consumed terminal sentence punctuation, keep it outside math mode.
    while end > relation_end and text[end - 1].isspace():
        end -= 1
    if end > relation_end and text[end - 1] == "." and not (
        end >= 2 and text[end - 2].isdigit() and end < len(text) and text[end].isdigit()
    ):
        end -= 1
    return end


def _convert_unwrapped_relations(text: str) -> str:
    """Convert obvious equation/reaction spans while leaving surrounding prose untouched."""
    # Work right-to-left so replacements do not invalidate earlier indices.
    matches = list(_RELATION_RE.finditer(text))
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []

    for match in reversed(matches):
        start = _lhs_start(text, match.start())
        end = _rhs_end(text, match.end())
        if end <= match.end() or start >= match.start():
            continue
        # Avoid overlapping replacements if a clause contains more than one relation.
        if any(not (end <= a or start >= b) for a, b in occupied):
            continue
        raw = text[start:end].strip()
        # Require content on both sides of the relation.
        rel = _RELATION_RE.search(raw)
        if not rel or not raw[:rel.start()].strip() or not raw[rel.end():].strip():
            continue
        replacements.append((start, end, _latexify_expression(raw)))
        occupied.append((start, end))

    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def normalize_latex_math(value: str) -> str:
    """
    Convert obvious equations/reactions to inline LaTeX while preserving non-math text.

    Existing LaTeX spans are preserved exactly. The function is intentionally
    conservative: it targets explicit relations (=, inequalities, arrows) and common
    mathematical symbols rather than rewriting ordinary prose that contains numbers.
    """
    text = "" if value is None else str(value)
    if not text:
        return text

    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"@@JOVE_LATEX_{len(protected) - 1}@@"

    working = _LATEX_SPAN_RE.sub(protect, text)
    working = _convert_unwrapped_relations(working)

    for idx, original in enumerate(protected):
        working = working.replace(f"@@JOVE_LATEX_{idx}@@", original)
    return working


def has_unwrapped_equation(value: str) -> bool:
    """Return True when an obvious equation/reaction remains outside LaTeX delimiters."""
    text = "" if value is None else str(value)
    if not text:
        return False
    stripped = _LATEX_SPAN_RE.sub("", text)
    return bool(_RELATION_RE.search(stripped))


def latex_to_plain_text(value: str) -> str:
    """Reduce LaTeX markup for duplicate/grounding text comparisons."""
    text = "" if value is None else str(value)
    text = text.replace(r"\(", " ").replace(r"\)", " ").replace(r"\[", " ").replace(r"\]", " ")
    text = re.sub(r"\$([^$]+)\$", r"\1", text)
    text = re.sub(r"\\text\{([^{}]*)\}", r"\1", text)
    # Keep command names as readable words for Greek symbols; drop structural commands.
    replacements = {
        r"\times": " ", r"\div": " ", r"\cdot": " ",
        r"\leq": " ", r"\geq": " ", r"\neq": " ", r"\approx": " ",
        r"\pm": " ", r"\rightarrow": " ", r"\leftrightarrow": " ", r"\rightleftharpoons": " ",
        r"\\sqrt": " ", r"\\frac": " ",
    }
    for raw, repl in replacements.items():
        text = text.replace(raw, repl)
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", text)
    text = re.sub(r"[{}_^]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
