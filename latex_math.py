from __future__ import annotations

import re

# Required output convention:
#   (Actual equation) \(LaTeX code\)
# Example:
#   (E = mc²) \(E = mc^{2}\)
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
_REVERSE_OPERATOR_MAP = {latex: raw for raw, latex in _OPERATOR_MAP.items()}

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

# Deterministic conversion targets obvious equations/reactions. The generation prompt
# is stricter and requires every mathematical expression to use the dual format.
_RELATION_RE = re.compile(r"(?<!\\)(=|≤|≥|≠|≈|→|↔|⇌)")


def _replace_unicode_scripts(expr: str) -> str:
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

    expr = re.sub(r"√\s*\(([^()]+)\)", r"\\sqrt{\1}", expr)
    expr = re.sub(r"√\s*([A-Za-z0-9]+)", r"\\sqrt{\1}", expr)

    # Common chemical formula digits become explicit subscripts:
    # H2O -> H_{2}O, CO2 -> CO_{2}.
    expr = re.sub(r"([A-Z][a-z]?)(\d+)", r"\1_{\2}", expr)
    expr = re.sub(r"(?<!\\)%", r"\\%", expr)

    return r"\(" + expr + r"\)"


def _latex_readable(span: str) -> str:
    """Create a readable equation for the parenthesized first half of dual output."""
    inner = str(span or "")
    if inner.startswith(r"\(") and inner.endswith(r"\)"):
        inner = inner[2:-2]
    elif inner.startswith(r"\[") and inner.endswith(r"\]"):
        inner = inner[2:-2]
    elif inner.startswith("$") and inner.endswith("$"):
        inner = inner[1:-1]

    inner = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", r"\1", inner)
    inner = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", inner)
    inner = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", inner)
    for latex, raw in sorted(_REVERSE_OPERATOR_MAP.items(), key=lambda pair: -len(pair[0])):
        inner = inner.replace(latex, raw)
    inner = inner.replace(r"\%", "%")
    inner = re.sub(r"\^\{([^{}]+)\}", r"^\1", inner)
    inner = re.sub(r"_\{([^{}]+)\}", r"_\1", inner)
    inner = inner.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", inner).strip()


def _matching_open_parenthesis(text: str, close_index: int) -> int:
    depth = 0
    for idx in range(close_index, -1, -1):
        char = text[idx]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _dual_ranges(text: str) -> list[tuple[int, int]]:
    """Locate already-correct '(actual) \\(latex\\)' spans so normalization is idempotent."""
    ranges: list[tuple[int, int]] = []
    for match in _LATEX_SPAN_RE.finditer(text):
        idx = match.start() - 1
        while idx >= 0 and text[idx].isspace():
            idx -= 1
        if idx < 0 or text[idx] != ")":
            continue
        open_idx = _matching_open_parenthesis(text, idx)
        if open_idx < 0:
            continue
        actual = text[open_idx + 1 : idx]
        if _RELATION_RE.search(actual) or re.search(r"[+\-×÷·/^√]", actual):
            ranges.append((open_idx, match.end()))
    return ranges


def _protect_ranges(
    text: str,
    ranges: list[tuple[int, int]],
    prefix: str,
) -> tuple[str, list[tuple[str, str]]]:
    protected: list[tuple[str, str]] = []
    for start, end in sorted(ranges, reverse=True):
        token = f"@@{prefix}_{len(protected)}@@"
        protected.append((token, text[start:end]))
        text = text[:start] + token + text[end:]
    return text, protected


def _lhs_start(text: str, relation_start: int) -> int:
    prefix = text[:relation_start]
    boundary = max(prefix.rfind(ch) for ch in ".,;:?!|\n") + 1
    candidate = prefix[boundary:]

    # Split a sentence containing two equations: "x = 2 and y = 3".
    lower_candidate = candidate.lower()
    and_pos = lower_candidate.rfind(" and ")
    or_pos = lower_candidate.rfind(" or ")
    conj_pos = max(and_pos, or_pos)
    if conj_pos >= 0:
        token_len = 5 if conj_pos == and_pos else 4
        boundary += conj_pos + token_len
        candidate = prefix[boundary:]

    cue = re.search(
        r"\b(?:use|using|given|where|when|if|is|calculate|determine|formula|equation|"
        r"reaction\s+is|formula\s+is|equation\s+is)\s+(.+)$",
        candidate,
        re.I,
    )
    if cue:
        return boundary + cue.start(1)

    # A reaction/multi-term expression should retain the complete left side.
    if re.search(r"\s[+\-*/×÷·]\s", candidate) or _RELATION_RE.search(candidate):
        return boundary

    match = re.search(
        r"([A-Za-z][A-Za-z0-9_]*(?:\s*\([A-Za-z0-9_]+\))?|"
        r"[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,3})\s*$",
        candidate,
    )
    if match:
        return boundary + match.start(1)
    return boundary


def _rhs_end(text: str, relation_end: int) -> int:
    tail = text[relation_end:]
    stops = [
        idx
        for idx in (
            tail.find(","),
            tail.find(";"),
            tail.find("?"),
            tail.find("!"),
            tail.find("|"),
        )
        if idx >= 0
    ]
    word_stop = re.search(
        r"\s+\b(?:where|when|if|then|what|which|who|how|calculate|determine|"
        r"correct|true|false|to|for|in|and|or)\b",
        tail,
        re.I,
    )
    if word_stop:
        stops.append(word_stop.start())

    end = relation_end + (min(stops) if stops else len(tail))
    while end > relation_end and text[end - 1].isspace():
        end -= 1
    if end > relation_end and text[end - 1] == ".":
        end -= 1
    return end


def _convert_unwrapped_relations(text: str) -> str:
    matches = list(_RELATION_RE.finditer(text))
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []

    for match in reversed(matches):
        start = _lhs_start(text, match.start())
        end = _rhs_end(text, match.end())
        if end <= match.end() or start >= match.start():
            continue
        if any(not (end <= a or start >= b) for a, b in occupied):
            continue

        segment = text[start:end]
        lead = re.match(r"\s*", segment).group(0)
        raw = segment.strip()
        relation = _RELATION_RE.search(raw)
        if (
            not relation
            or not raw[: relation.start()].strip()
            or not raw[relation.end() :].strip()
        ):
            continue

        replacements.append(
            (start, end, lead + f"({raw}) {_latexify_expression(raw)}")
        )
        occupied.append((start, end))

    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def normalize_latex_math(value: str) -> str:
    """
    Enforce the review-friendly dual representation:
        (Actual equation) \\(LaTeX code\\)

    - Already-correct dual spans remain unchanged.
    - Existing LaTeX-only spans gain a readable parenthesized form.
    - Obvious raw equations/reactions gain both the original readable equation and LaTeX.
    - Ordinary prose is left alone.
    """
    text = "" if value is None else str(value)
    if not text:
        return text

    working, duals = _protect_ranges(text, _dual_ranges(text), "JOVE_DUAL")

    latex_only: list[tuple[str, str]] = []

    def protect_latex(match: re.Match[str]) -> str:
        token = f"@@JOVE_LATEX_{len(latex_only)}@@"
        latex_only.append((token, match.group(0)))
        return token

    working = _LATEX_SPAN_RE.sub(protect_latex, working)
    working = _convert_unwrapped_relations(working)

    for token, span in latex_only:
        working = working.replace(token, f"({_latex_readable(span)}) {span}")
    for token, dual in duals:
        working = working.replace(token, dual)
    return working


def has_unwrapped_equation(value: str) -> bool:
    """True only when an obvious relation remains outside the required dual/LaTeX format."""
    text = "" if value is None else str(value)
    if not text:
        return False
    stripped, _ = _protect_ranges(text, _dual_ranges(text), "JOVE_DUALCHECK")
    stripped = _LATEX_SPAN_RE.sub("", stripped)
    return bool(_RELATION_RE.search(stripped))


def latex_to_plain_text(value: str) -> str:
    """Reduce dual/LaTeX formatting to one readable copy for duplicate/grounding checks."""
    text = "" if value is None else str(value)
    if not text:
        return text

    for start, end in sorted(_dual_ranges(text), reverse=True):
        segment = text[start:end]
        latex_match = _LATEX_SPAN_RE.search(segment)
        if latex_match:
            prefix = segment[: latex_match.start()].rstrip()
            actual = (
                prefix[1:-1].strip()
                if prefix.startswith("(") and prefix.endswith(")")
                else prefix
            )
        else:
            actual = segment
        text = text[:start] + actual + text[end:]

    text = _LATEX_SPAN_RE.sub(lambda match: _latex_readable(match.group(0)), text)
    return re.sub(r"\s+", " ", text).strip()
