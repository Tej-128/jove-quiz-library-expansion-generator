from __future__ import annotations

from latex_math import has_unwrapped_latex_required
from quiz_generator import validate_generated_question


VALID_DUAL = [
    r"(C₆H₁₂O₆) \(C_{6}H_{12}O_{6}\)",
    r"(200 °C) \(200\,^{\circ}\mathrm{C}\)",
    r"(ρ) \(\rho\)",
    r"(Ca²⁺) \(Ca^{2+}\)",
    r"(P = ρgh) \(P = \rho gh\)",
    r"(1/2) \(\frac{1}{2}\)",
    r"(3:1) \(3:1\)",
    r"(1.2 × 10³) \(1.2 \times 10^{3}\)",
    r"(m²) \(m^{2}\)",
    r"(mol L⁻¹) \(\mathrm{mol}\,\mathrm{L}^{-1}\)",
    r"(√x) \(\sqrt{x}\)",
]

INVALID_RAW = [
    "C6H12O6",
    "C₆H₁₂O₆",
    "200 °C",
    "ρ",
    "Ca²⁺",
    "P = ρgh",
    "x^2",
    "1/2",
    "3:1",
    "1.2e-3",
    "1.2 × 10³",
    "m²",
    "mol L⁻¹",
    "√x",
    "Na+",
    "C-14",
    "variables g and h",
    r"$E=mc^2$",
    r"\(E=mc^2\)",
]

EXEMPT_PROSE = [
    "50% of the sample",
    "Lesson 10649",
    "Chapter 3",
    "5 kg",
    "DNA",
    "ATP",
    "pH",
    "receptor-mediated endocytosis",
    "frequency-dependent selection",
    "A-B testing",
]


def _base(qtype: str) -> dict:
    return {
        "lesson_id": "99999",
        "question_index": "1",
        "question_content": "Choose the statement supported by the lesson.",
        "question_type": qtype,
        "option_1": "First statement",
        "option_2": "Second statement",
        "option_3": "Third statement",
        "option_4": "Fourth statement",
        "right_answer": "1",
    }


def _strict_error(errors: list[str]) -> bool:
    return any("STRICT LATEX FORMAT FAILURE" in error for error in errors)


def check_detector() -> None:
    for text in VALID_DUAL:
        assert not has_unwrapped_latex_required(text), f"valid dual rejected: {text}"

    for text in INVALID_RAW:
        assert has_unwrapped_latex_required(text), f"required notation missed: {text}"

    for text in EXEMPT_PROSE:
        assert not has_unwrapped_latex_required(text), f"ordinary prose false-positive: {text}"


def check_all_question_types() -> None:
    source = "lesson source statement scientific content"

    q = _base("Single Correct")
    q["question_content"] = "Which statement describes C6H12O6?"
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Single Correct did not hard-fail raw notation"

    q = _base("Multi Correct")
    q["right_answer"] = "1,2"
    q["option_1"] = "C₆H₁₂O₆"
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Multi Correct did not hard-fail raw notation"

    q = _base("True or False")
    q["question_content"] = "The symbol ρ is used in the lesson."
    q["option_1"], q["option_2"], q["option_3"], q["option_4"] = "TRUE", "FALSE", "", ""
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "True or False did not hard-fail raw notation"

    q = _base("Fill in the Blanks")
    q["question_content"] = "The formula is ___[1]___."
    q["option_1"] = q["option_2"] = q["option_3"] = q["option_4"] = ""
    q["right_answer"] = "Ca²⁺"
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Fill in the Blanks answer did not hard-fail raw notation"

    q = _base("Dropdown")
    q["question_content"] = "Select ---[dropdown 1]---."
    q["option_1"] = "C6H12O6 | glucose | water | protein"
    q["option_2"] = q["option_3"] = q["option_4"] = ""
    q["right_answer"] = "1"
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Dropdown choice did not hard-fail raw notation"

    q = _base("Match the following")
    q["question_content"] = "Match each item."
    q["option_1"] = "Density | ρ"
    q["option_2"] = "Mass | amount"
    q["option_3"] = "Volume | space"
    q["option_4"] = ""
    q["right_answer"] = ""
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Match the following pair did not hard-fail raw notation"

    q = _base("Categorisation")
    q["question_content"] = "Categorise the items."
    q["option_1"] = "Formula | H2O | protein"
    q["option_2"] = "Term | cell | tissue"
    q["option_3"] = q["option_4"] = ""
    q["right_answer"] = ""
    _, _, errors = validate_generated_question(q, 1, "99999", source)
    assert _strict_error(errors), "Categorisation item did not hard-fail raw notation"


if __name__ == "__main__":
    check_detector()
    check_all_question_types()
    print("PASS - strict LaTeX coverage regression checks")
