from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any, Callable

GENERATED_TYPES = [
    "Multi Correct",
    "True or False",
    "Fill in the Blanks",
    "Dropdown",
    "Match the following",
    "Categorisation",
]
PER_TYPE = 3
TOTAL_GENERATED = len(GENERATED_TYPES) * PER_TYPE
MAX_RETRIES = 3
NEAR_DUPLICATE_THRESHOLD = 0.88
GROUNDING_MIN_OVERLAP = 0.25

REQUIRED_FIELDS = [
    "lesson_id",
    "question_index",
    "question_content",
    "question_type",
    "option_1",
    "option_2",
    "option_3",
    "option_4",
    "right_answer",
]

STOPWORDS = {
    "about", "above", "after", "again", "against", "along", "also", "among", "because",
    "before", "being", "between", "blank", "cannot", "choose", "correct", "could", "describe",
    "during", "each", "following", "from", "given", "have", "into", "more", "most", "only",
    "option", "question", "should", "statement", "than", "that", "their", "then", "there", "these",
    "this", "those", "through", "true", "false", "using", "what", "when", "where", "which", "while",
    "with", "would", "your", "dropdown", "match", "categorise", "categorize",
}

SYSTEM_PROMPT = """You are an expert educational quiz writer for JoVE.

ABSOLUTE SOURCE RULES
1. Use ONLY the supplied PageText and Transcript/CC content. Do not use outside knowledge.
2. Do not introduce facts, examples, numbers, names, definitions, or relationships absent from the supplied source.
3. Avoid duplicating or closely paraphrasing any existing quiz question supplied in the exclusion list.
4. No explanations or rationales are required.
5. Return ONLY a valid JSON array. No markdown, code fences, or prose.

OUTPUT SCHEMA
Each object must contain exactly:
lesson_id, question_index, question_content, question_type, option_1, option_2, option_3, option_4, right_answer.

QUESTION TYPES
Multi Correct:
- Exactly four distinct non-empty options.
- Exactly 2 or 3 options are correct.
- right_answer is comma-separated option positions, e.g. "1,3".
- Correct positions must vary across the three generated questions.

True or False:
- option_1 exactly TRUE; option_2 exactly FALSE; option_3 and option_4 empty.
- right_answer is "1" for TRUE or "2" for FALSE.
- Across the three True/False questions, include at least one TRUE and at least one FALSE answer.

Fill in the Blanks:
- question_content contains exactly one ___[1]___ placeholder.
- option_1 through option_4 are empty.
- right_answer is the exact correct word or phrase.

Dropdown:
- question_content contains one or two placeholders: ---[dropdown 1]--- and optionally ---[dropdown 2]---.
- option_1 contains exactly four pipe-separated choices for dropdown 1.
- option_2 contains exactly four pipe-separated choices only when dropdown 2 exists.
- option_3 and option_4 are empty.
- right_answer stores correct choice positions, e.g. "3" or "2,4".
- Correct positions must vary across the three Dropdown questions.

Match the following:
- Use exactly 3 or 4 pairs.
- Each used option field is exactly: Left term | Right term.
- right_answer is empty.

Categorisation:
- Use 2 to 4 categories.
- Each used option field is: Category Name | item1 | item2, with 2 to 4 items after the category.
- right_answer is empty.

Do not use the pipe character in question_content. Pipes are reserved only for Dropdown, Match the following, and Categorisation option fields.
"""

QA_SYSTEM_PROMPT = """You are a strict JoVE quiz quality reviewer.
Use ONLY the supplied lesson source. Do not use outside knowledge.
Return only a JSON array. For every question return exactly:
question_index, status, issue

status must be one of: pass, review, fail.
- pass: clearly supported, answer is correct, wording is unambiguous, and no meaningful duplicate exists.
- review: probably usable but source support, phrasing, ambiguity, or confidence is not strong enough for automatic approval.
- fail: clearly unsupported, incorrect, contradictory, malformed, or materially duplicated.
Keep issue concise. Do not rewrite the question.
"""


class LLMRequestError(RuntimeError):
    pass


def _looks_like_unsupported_param_error(exc: Exception, param_name: str) -> bool:
    message = str(exc).lower()
    return param_name.lower() in message and (
        "unsupported" in message or "unrecognized" in message or "not supported" in message
    )


def call_llm(system: str, user: str, api_key: str, model: str) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise LLMRequestError("OpenAI Python SDK is missing or incompatible.") from exc

    client = OpenAI(api_key=api_key)
    args = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        try:
            response = client.chat.completions.create(**args, max_completion_tokens=12000)
        except Exception as exc:
            if not _looks_like_unsupported_param_error(exc, "max_completion_tokens"):
                raise
            response = client.chat.completions.create(**args, max_tokens=12000)
    except Exception as exc:
        raise LLMRequestError(str(exc)) from exc
    return response.choices[0].message.content or ""


def parse_llm_json(raw: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?", "", raw or "").strip().strip("`").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON array found. First 300 chars: {cleaned[:300]}")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("Model response is not a JSON array.")
    return data


def _as_string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"---\[dropdown\s*\d+\]---", " dropdownplaceholder ", text)
    text = re.sub(r"___\[1\]___", " blankplaceholder ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return set(_norm(text).split())


def _token_jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _significant_terms(text: str) -> set[str]:
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", text).lower()
    return {
        token for token in normalized.split()
        if len(token) >= 4 and token not in STOPWORDS and not token.isdigit()
    }


def _split_pipe(value: str) -> list[str]:
    return [piece.strip() for piece in str(value).split("|")]


def _parse_answers(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _distinct(values: list[str]) -> bool:
    normalized = [_norm(v) for v in values]
    return len(normalized) == len(set(normalized))


def _question_text(q: dict[str, Any]) -> str:
    return " ".join(_as_string(q.get(k, "")) for k in ["question_content", "option_1", "option_2", "option_3", "option_4"])


def _question_snippet(q: dict[str, Any], max_chars: int = 260) -> str:
    text = re.sub(r"\s+", " ", _question_text(q)).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return f"[{q.get('question_type', '?')}] {text}"


def _is_duplicate(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> bool:
    candidate_q = _norm(_as_string(candidate.get("question_content", "")))
    if not candidate_q:
        return True
    for other in existing:
        other_q = _norm(_as_string(other.get("question_content", "")))
        if candidate_q == other_q:
            return True
        if _token_jaccard(candidate_q, other_q) >= NEAR_DUPLICATE_THRESHOLD:
            return True
    return False


def _grounding(candidate: dict[str, Any], source_text: str) -> tuple[str, str]:
    source_terms = _significant_terms(source_text)
    q_terms = _significant_terms(_question_text(candidate))
    if not source_terms:
        return "fail", "Lesson source is empty."
    if not q_terms:
        return "review", "No significant terms available for deterministic grounding check."
    overlap = q_terms & source_terms
    ratio = len(overlap) / max(1, len(q_terms))
    missing = sorted(q_terms - source_terms)[:10]
    if ratio < GROUNDING_MIN_OVERLAP:
        return "fail", "Low source-term overlap; terms not found include: " + ", ".join(missing)
    # Do not flag a question merely because a few words are paraphrased or are
    # structural wording. AI accuracy QA handles semantic support. Only borderline
    # deterministic overlap should become a manual-review flag.
    if ratio < 0.55:
        return "review", "Borderline source-term overlap; terms not found include: " + ", ".join(missing)
    return "pass", ""


def validate_generated_question(
    raw: dict[str, Any],
    index: int,
    lesson_id: str,
    source_text: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, warnings, [f"Q{index}: item is not an object"]

    q = {field: _as_string(raw.get(field, "")) for field in REQUIRED_FIELDS}
    q["lesson_id"] = lesson_id
    q["question_index"] = index
    q["origin"] = "generated"
    q["review_level"] = "none"
    q["review_reason"] = ""

    qtype = q["question_type"]
    content = q["question_content"]
    if qtype not in GENERATED_TYPES:
        errors.append(f"Unknown generated question type '{qtype}'.")
        return q, warnings, errors
    if not content:
        errors.append("Question content is empty.")
    if "|" in content:
        errors.append("Question content contains illegal pipe character.")

    for key in ["option_1", "option_2", "option_3", "option_4"]:
        if q[key].lower() == "none":
            q[key] = ""

    if qtype == "Multi Correct":
        options = [q[f"option_{i}"] for i in range(1, 5)]
        if any(not o for o in options) or not _distinct(options):
            errors.append("Multi Correct requires four distinct non-empty options.")
        answers = _parse_answers(q["right_answer"])
        if len(answers) not in {2, 3} or len(set(answers)) != len(answers) or any(a not in {"1", "2", "3", "4"} for a in answers):
            errors.append("Multi Correct requires 2 or 3 unique answer positions from 1-4.")
        else:
            q["right_answer"] = ",".join(sorted(answers, key=int))

    elif qtype == "True or False":
        q["option_1"], q["option_2"], q["option_3"], q["option_4"] = "TRUE", "FALSE", "", ""
        if q["right_answer"] not in {"1", "2"}:
            errors.append("True or False right_answer must be 1 or 2.")

    elif qtype == "Fill in the Blanks":
        q["option_1"] = q["option_2"] = q["option_3"] = q["option_4"] = ""
        placeholders = re.findall(r"___\[\d+\]___", content)
        if placeholders != ["___[1]___"]:
            errors.append("Fill in the Blanks must contain exactly one ___[1]___ placeholder.")
        if not q["right_answer"]:
            errors.append("Fill in the Blanks requires right_answer.")

    elif qtype == "Dropdown":
        nums = [int(x) for x in re.findall(r"---\[dropdown\s+(\d+)\]---", content)]
        if nums not in ([1], [1, 2]):
            errors.append("Dropdown placeholders must be exactly [1] or [1,2].")
        answers = _parse_answers(q["right_answer"])
        if len(answers) != len(nums) or any(a not in {"1", "2", "3", "4"} for a in answers):
            errors.append("Dropdown answer positions do not match placeholder count.")
        for idx2 in range(1, len(nums) + 1):
            parts = _split_pipe(q[f"option_{idx2}"])
            if len(parts) != 4 or any(not p for p in parts) or not _distinct(parts):
                errors.append(f"Dropdown option_{idx2} must contain four distinct pipe-separated choices.")
            else:
                q[f"option_{idx2}"] = " | ".join(parts)
        for idx2 in range(len(nums) + 1, 5):
            if q[f"option_{idx2}"]:
                errors.append(f"Dropdown option_{idx2} must be empty.")

    elif qtype == "Match the following":
        q["right_answer"] = ""
        used = [q[f"option_{i}"] for i in range(1, 5) if q[f"option_{i}"]]
        if len(used) not in {3, 4}:
            errors.append("Match the following requires 3 or 4 pairs.")
        for idx2 in range(1, 5):
            value = q[f"option_{idx2}"]
            if not value:
                continue
            parts = _split_pipe(value)
            if len(parts) != 2 or any(not p for p in parts):
                errors.append(f"Match option_{idx2} must be 'Left term | Right term'.")
            else:
                q[f"option_{idx2}"] = " | ".join(parts)

    elif qtype == "Categorisation":
        q["right_answer"] = ""
        used = [q[f"option_{i}"] for i in range(1, 5) if q[f"option_{i}"]]
        if len(used) < 2 or len(used) > 4:
            errors.append("Categorisation requires 2 to 4 categories.")
        for idx2 in range(1, 5):
            value = q[f"option_{idx2}"]
            if not value:
                continue
            parts = _split_pipe(value)
            if len(parts) < 3 or len(parts) > 5 or any(not p for p in parts):
                errors.append(f"Categorisation option_{idx2} must be category plus 2 to 4 items.")
            else:
                q[f"option_{idx2}"] = " | ".join(parts)

    if not errors:
        grounding_level, grounding_reason = _grounding(q, source_text)
        if grounding_level == "fail":
            errors.append(grounding_reason)
        elif grounding_level == "review":
            warnings.append(grounding_reason)

    return q, warnings, errors


def _source_prompt(pt_text: str, transcript_text: str) -> str:
    return (
        "[PAGETEXT]\n" + (pt_text or "") + "\n\n"
        "[TRANSCRIPT OR CLEANED CC]\n" + (transcript_text or "")
    )


def _budget_text(missing: dict[str, int] | None = None) -> str:
    budget = missing or {t: PER_TYPE for t in GENERATED_TYPES}
    return "\n".join(f'- {count} question(s) of type "{qtype}"' for qtype, count in budget.items() if count > 0)


def build_generation_prompt(
    lesson_id: str,
    lesson_title: str,
    subject: str,
    pt_text: str,
    transcript_text: str,
    existing_questions: list[dict[str, Any]],
    missing: dict[str, int] | None = None,
    answer_balance_note: str = "",
) -> str:
    exclusions = "\n".join("- " + _question_snippet(q) for q in existing_questions[:100]) or "- None"
    budget = missing or {t: PER_TYPE for t in GENERATED_TYPES}
    count = sum(budget.values())
    return f"""Generate exactly {count} ADDITIONAL quiz questions for one lesson.

Subject: {subject}
Lesson ID: {lesson_id}
Lesson title: {lesson_title}

Exact required breakdown:
{_budget_text(budget)}

Important:
- Do NOT generate Single Correct questions.
- These questions will be appended after the existing quiz.
- Vary answer positions naturally. Do not create a predictable answer-position pattern.
- For True/False, make the three-question set include both TRUE and FALSE answers whenever three are requested.
- Keep wording concise and suitable for the subject and lesson level.
{answer_balance_note}

Existing quiz questions that MUST NOT be duplicated or closely paraphrased:
{exclusions}

SOURCE MATERIAL (the only allowed source):
{_source_prompt(pt_text, transcript_text)}

Return the JSON array only.
"""


def _shuffle_multi(question: dict[str, Any], rng: random.Random) -> None:
    answers = {int(a) for a in _parse_answers(question.get("right_answer", "")) if a.isdigit()}
    if not answers:
        return
    old = [question.get(f"option_{i}", "") for i in range(1, 5)]
    permutation = [1, 2, 3, 4]
    rng.shuffle(permutation)
    new = [old[i - 1] for i in permutation]
    new_answers = [pos for pos, old_pos in enumerate(permutation, 1) if old_pos in answers]
    for i, value in enumerate(new, 1):
        question[f"option_{i}"] = value
    question["right_answer"] = ",".join(map(str, sorted(new_answers)))


def _shuffle_dropdown(question: dict[str, Any], rng: random.Random) -> None:
    answers = _parse_answers(question.get("right_answer", ""))
    for dropdown_idx, answer in enumerate(answers, 1):
        if answer not in {"1", "2", "3", "4"}:
            continue
        key = f"option_{dropdown_idx}"
        choices = _split_pipe(question.get(key, ""))
        if len(choices) != 4:
            continue
        correct = choices[int(answer) - 1]
        rng.shuffle(choices)
        question[key] = " | ".join(choices)
        question_answer_pos = str(choices.index(correct) + 1)
        answers[dropdown_idx - 1] = question_answer_pos
    question["right_answer"] = ",".join(answers)


def _shuffle_nonpositional(question: dict[str, Any], rng: random.Random) -> None:
    qtype = question.get("question_type")
    if qtype == "Match the following":
        used = [question[f"option_{i}"] for i in range(1, 5) if question.get(f"option_{i}")]
        rng.shuffle(used)
        for i in range(1, 5):
            question[f"option_{i}"] = used[i - 1] if i <= len(used) else ""
    elif qtype == "Categorisation":
        used = [question[f"option_{i}"] for i in range(1, 5) if question.get(f"option_{i}")]
        shuffled_categories: list[str] = []
        for value in used:
            parts = _split_pipe(value)
            if len(parts) >= 3:
                head, items = parts[0], parts[1:]
                rng.shuffle(items)
                shuffled_categories.append(" | ".join([head] + items))
            else:
                shuffled_categories.append(value)
        rng.shuffle(shuffled_categories)
        for i in range(1, 5):
            question[f"option_{i}"] = shuffled_categories[i - 1] if i <= len(shuffled_categories) else ""


def randomize_generated_answers(questions: list[dict[str, Any]]) -> None:
    rng = random.SystemRandom()
    for question in questions:
        if question.get("question_type") == "Multi Correct":
            _shuffle_multi(question, rng)
        elif question.get("question_type") == "Dropdown":
            _shuffle_dropdown(question, rng)
        else:
            _shuffle_nonpositional(question, rng)

    # Avoid identical Multi Correct answer-key patterns across all 3.
    multi = [q for q in questions if q.get("question_type") == "Multi Correct"]
    for _ in range(24):
        patterns = [q.get("right_answer", "") for q in multi]
        if len(patterns) <= 1 or len(set(patterns)) > 1:
            break
        _shuffle_multi(multi[-1], rng)

    # Avoid identical Dropdown correct positions across all 3.
    dropdown = [q for q in questions if q.get("question_type") == "Dropdown"]
    for _ in range(24):
        patterns = [q.get("right_answer", "") for q in dropdown]
        if len(patterns) <= 1 or len(set(patterns)) > 1:
            break
        _shuffle_dropdown(dropdown[-1], rng)


def _counts(questions: list[dict[str, Any]]) -> dict[str, int]:
    return {qtype: sum(1 for q in questions if q.get("question_type") == qtype) for qtype in GENERATED_TYPES}


def _missing(questions: list[dict[str, Any]]) -> dict[str, int]:
    counts = _counts(questions)
    return {qtype: max(0, PER_TYPE - counts.get(qtype, 0)) for qtype in GENERATED_TYPES}


def _true_false_distribution_flag(questions: list[dict[str, Any]]) -> str:
    tf = [q for q in questions if q.get("question_type") == "True or False"]
    if len(tf) >= 2 and len({q.get("right_answer") for q in tf}) == 1:
        return "All generated True/False questions have the same answer; manual review recommended."
    return ""


def _apply_review(question: dict[str, Any], level: str, reason: str) -> None:
    rank = {"none": 0, "review": 1, "fail": 2}
    current = question.get("review_level", "none")
    if rank.get(level, 0) > rank.get(current, 0):
        question["review_level"] = level
    if reason:
        existing = question.get("review_reason", "")
        if reason not in existing:
            question["review_reason"] = (existing + "; " + reason).strip("; ")


def build_accuracy_review_prompt(subject: str, lesson_id: str, source_text: str, questions: list[dict[str, Any]]) -> str:
    payload = [
        {
            k: q.get(k, "")
            for k in ["question_index", "question_type", "question_content", "option_1", "option_2", "option_3", "option_4", "right_answer"]
        }
        for q in questions
    ]
    return f"""Subject: {subject}
Lesson ID: {lesson_id}

SOURCE MATERIAL:
{source_text}

QUESTIONS:
{json.dumps(payload, ensure_ascii=False)}

Review every question. Return JSON array only.
"""


def apply_ai_accuracy_qa(
    questions: list[dict[str, Any]],
    subject: str,
    lesson_id: str,
    source_text: str,
    api_key: str,
    model: str,
) -> list[str]:
    warnings: list[str] = []
    if not questions:
        return warnings
    try:
        raw = call_llm(QA_SYSTEM_PROMPT, build_accuracy_review_prompt(subject, lesson_id, source_text, questions), api_key, model)
        reviews = parse_llm_json(raw)
    except Exception as exc:
        warnings.append(f"AI accuracy QA could not run: {exc}")
        for q in questions:
            _apply_review(q, "review", "AI accuracy QA was unavailable; manual spot-check recommended.")
        return warnings

    by_index: dict[int, dict[str, Any]] = {}
    for item in reviews:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(str(item.get("question_index", "")))
        except Exception:
            continue
        by_index[idx] = item

    for q in questions:
        idx = int(q.get("question_index", 0) or 0)
        review = by_index.get(idx)
        if not review:
            _apply_review(q, "review", "AI QA returned no result for this question.")
            continue
        status = str(review.get("status", "")).strip().lower()
        issue = str(review.get("issue", "")).strip()
        if status == "pass":
            continue
        if status == "fail":
            _apply_review(q, "fail", issue or "AI accuracy QA failed this question.")
        else:
            _apply_review(q, "review", issue or "AI accuracy QA requested manual review.")
    return warnings


def generate_additional_questions(
    *,
    lesson_id: str,
    lesson_title: str,
    subject: str,
    pt_text: str,
    transcript_text: str,
    existing_questions: list[dict[str, Any]],
    api_key: str,
    model: str,
    enable_ai_accuracy_qa: bool = True,
    progress_callback: Callable[[str, int | None], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_text = _source_prompt(pt_text, transcript_text)
    if not pt_text.strip() and not transcript_text.strip():
        raise ValueError("No PageText or Transcript/CC source text is available.")

    accepted: list[dict[str, Any]] = []
    generation_warnings: list[str] = []

    def notify(msg: str, pct: int | None = None):
        if progress_callback:
            progress_callback(msg, pct)

    for attempt in range(1, MAX_RETRIES + 1):
        missing = _missing(accepted)
        if not any(missing.values()):
            break
        notify(f"Generating additional questions (attempt {attempt}/{MAX_RETRIES})...", None)
        answer_balance_note = ""
        accepted_tf = [q for q in accepted if q.get("question_type") == "True or False"]
        if missing.get("True or False", 0) > 0 and accepted_tf:
            tf_answers = {q.get("right_answer") for q in accepted_tf}
            if len(tf_answers) == 1:
                current = next(iter(tf_answers))
                opposite = "2" if current == "1" else "1"
                answer_balance_note = (
                    f"- True/False balance requirement: at least one remaining True/False question MUST have right_answer={opposite} "
                    f"so the lesson does not use the same TRUE/FALSE answer position for every question."
                )

        prompt = build_generation_prompt(
            lesson_id=lesson_id,
            lesson_title=lesson_title,
            subject=subject,
            pt_text=pt_text,
            transcript_text=transcript_text,
            existing_questions=existing_questions + accepted,
            missing=missing,
            answer_balance_note=answer_balance_note,
        )
        try:
            raw = call_llm(SYSTEM_PROMPT, prompt, api_key, model)
            candidates = parse_llm_json(raw)
        except Exception as exc:
            generation_warnings.append(f"Generation attempt {attempt} failed: {exc}")
            continue

        wanted = {qtype for qtype, count in missing.items() if count > 0}
        for raw_candidate in candidates:
            qtype = str(raw_candidate.get("question_type", "")).strip() if isinstance(raw_candidate, dict) else ""
            if qtype not in wanted:
                continue
            if _counts(accepted).get(qtype, 0) >= PER_TYPE:
                continue
            clean, warnings, errors = validate_generated_question(
                raw_candidate,
                index=len(accepted) + 1,
                lesson_id=lesson_id,
                source_text=source_text,
            )
            if errors:
                generation_warnings.append(
                    f"Rejected {qtype or 'unknown'} candidate: " + " | ".join(errors[:3])
                )
                continue
            if _is_duplicate(clean, existing_questions + accepted):
                generation_warnings.append(f"Rejected near-duplicate generated {qtype} question.")
                continue
            if qtype == "True or False":
                prior_tf = [q for q in accepted if q.get("question_type") == "True or False"]
                if len(prior_tf) >= 2 and len({q.get("right_answer") for q in prior_tf}) == 1:
                    if clean.get("right_answer") == prior_tf[0].get("right_answer"):
                        generation_warnings.append("Rejected True/False candidate to prevent all three answers using the same option.")
                        continue
            if warnings:
                _apply_review(clean, "review", "; ".join(warnings))
            accepted.append(clean)

    missing = _missing(accepted)
    if any(missing.values()):
        raise RuntimeError(
            "Could not produce the required 18 structurally valid questions. Missing: "
            + ", ".join(f"{qtype}={count}" for qtype, count in missing.items() if count)
        )

    # Put generated questions in a stable type order, 3 each, then randomize only
    # answer/choice positions inside each question. This makes QC easy while still
    # preventing predictable answer placement.
    ordered: list[dict[str, Any]] = []
    for qtype in GENERATED_TYPES:
        ordered.extend([q for q in accepted if q.get("question_type") == qtype][:PER_TYPE])
    accepted = ordered
    for index, q in enumerate(accepted, 1):
        q["question_index"] = index

    randomize_generated_answers(accepted)

    tf_flag = _true_false_distribution_flag(accepted)
    if tf_flag:
        generation_warnings.append(tf_flag)
        for q in accepted:
            if q.get("question_type") == "True or False":
                _apply_review(q, "review", tf_flag)

    if enable_ai_accuracy_qa:
        notify("Running source-grounding accuracy QA...", None)
        generation_warnings.extend(
            apply_ai_accuracy_qa(accepted, subject, lesson_id, source_text, api_key, model)
        )

    report = {
        "generated_count": len(accepted),
        "type_counts": _counts(accepted),
        "warnings": generation_warnings,
        "review_count": sum(1 for q in accepted if q.get("review_level") == "review"),
        "fail_count": sum(1 for q in accepted if q.get("review_level") == "fail"),
    }
    return accepted, report
