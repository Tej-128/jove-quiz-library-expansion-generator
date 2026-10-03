# JoVE Quiz Library Expansion Generator

A separate Streamlit workflow for expanding existing lesson-level Quiz Library quizzes without changing approved legacy questions.

## Locked workflow

For each lesson, the tool:

1. Detects the existing quiz Word document, PageText, and Transcript/CC using folder lesson IDs plus fuzzy role detection.
2. Parses every existing Single Correct / Multi Correct Word question.
3. Uses the `*` marker in existing Word options as the answer key. Existing questions are **not re-solved, rewritten, corrected, shuffled, or paraphrased**.
4. Removes document scaffolding such as `Chapter Title`, `Video Title`, `Writer`, `End-of-Lesson Quiz`, difficulty headings, and `End-of-Chapter Quiz Question` from quiz rows. The actual question under those headings is still transferred.
5. Converts the existing questions to the same 10-column Excel quiz schema used by the current JoVE quiz generator.
6. Uses only PageText + Transcript/cleaned CC as the source for new questions.
7. Generates exactly 21 additional questions per lesson:
   - 3 Single Correct
   - 3 Multi Correct
   - 3 True or False
   - 3 Fill in the Blanks
   - 3 Dropdown
   - 3 Match the following
   - 3 Categorisation
8. Prevents exact/near duplicates against both the existing quiz and the newly generated questions. AI QA also receives the existing quiz as an exclusion reference so it can catch semantic overlap that simple text matching misses.
9. Automatically replaces generated questions that fail QA or receive a substantive duplicate/source/ambiguity review flag, then re-runs QA. Up to two repair rounds are attempted before a question is left for manual review.
10. Randomizes answer/choice positions for generated choice-based formats while never shuffling existing Word questions.
11. Stores every equation/formula/reaction equation in quiz text as inline LaTeX code using `\( ... \)`. This applies to newly generated questions and to explicit equations encountered while transferring existing Word quiz text; ordinary prose is not rewritten.
12. Keeps `Match the following` and `Categorisation` in the established JoVE format: the correct relationship/category structure remains embedded in the option fields and `Right Answer` stays blank.
13. Appends all 21 generated questions below the existing quiz rows.
14. Colors only unresolved questions requiring manual review in Excel:
    - Yellow: manual review recommended, including duplicate-only issues that remain after repair attempts
    - Red: substantive critical issue such as unsupported/incorrect/ambiguous/malformed content
15. Produces one Excel workbook per lesson plus a batch summary CSV inside the final ZIP.

## Expected input

Recommended ZIP structure:

```text
Chapter Folder/
  16799/
    16799_UnderstandingFinance_PageText.docx
    16799_UnderstandingFinance_CC.vtt
    16799_UnderstandingFinance_Quiz.docx
  16800/
    16800_AreasOfFinance_PT.docx
    16800_AreasOfFinance_Transcript.docx
    16800_AreasOfFinance_Quiz.docx
```

The system does **not** require those exact filenames. It combines:

- numeric lesson folder / filename IDs,
- file extensions,
- filename keywords,
- fuzzy similarity,
- content clues.

Supported source variants include `PageText`, `Pagetext`, `PT`, `PTx`, `Transcript`, `Script`, `CC`, closed-caption `.vtt`, and similar naming variants.

If two files are genuinely ambiguous for the same role, the lesson is flagged instead of silently guessing.

## Closed captions vs transcript

Both are supported:

- `.vtt` CC files are cleaned by removing `WEBVTT`, timestamps, cue numbers, tags, and caption line breaks.
- `.docx` Transcript / Script files are read directly.

If the spoken content is the same, both feed the generator equivalent lesson content after cleaning.

## Existing quiz parsing

Existing Word quiz questions are detected structurally as:

```text
Question text
*A) correct option
B) distractor
C) distractor
D) distractor
```

One starred option becomes `Single Correct`.
Two or more starred options become `Multi Correct`.
The `*` and `A)/B)/C)/D)` structural prefixes are removed when mapping to Excel columns; option wording and order are preserved.

The number of existing questions is not fixed.

## Excel output

The `Quiz` sheet uses exactly:

```text
Chapter Name
Video ID
Question Index
Question Content
Question Type
Option 1
Option 2
Option 3
Option 4
Right Answer
```

Existing questions appear first, in original order. The 21 generated questions are appended in a clearly labeled AI-generated section below them. A second `QA Summary` sheet documents flags and source files.

## Streamlit deployment

1. Push this repository to GitHub. The repository may be public; do not commit lesson source files or secrets.
2. Deploy `app.py` in Streamlit Community Cloud.
3. Add both secrets:

```toml
OPENAI_API_KEY = "..."
APP_ACCESS_PASSWORD = "choose-a-strong-internal-password"
```

4. The app-level password gate is implemented in `app.py`, so the Streamlit deployment itself may remain public while the workspace fails closed until the correct password is entered. The password value is never stored in GitHub.
5. Upload a chapter ZIP, enter the subject, review detected file roles, and generate.

## JoVE UI

The Streamlit interface uses a JoVE-oriented visual system: JoVE red accents, internal-tool labeling, branded header, restrained white/gray surfaces, and a dedicated secure-workspace login screen. This changes presentation only; quiz parsing/generation logic is unchanged.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Resilient batch execution (v1.2.0)

- OpenAI requests use an explicit configurable timeout (\`JOVE_LLM_TIMEOUT_SECONDS\`, default 150 seconds) and one SDK retry by default (\`JOVE_LLM_MAX_RETRIES\`).
- The Streamlit batch updates a live lesson-results table after every lesson.
- Completed lesson outputs are checkpointed into a partial ZIP during the run.
- Per-lesson failures are shown immediately and the batch continues.
- Unexpected batch-orchestration failures surface a visible error and preserve completed outputs when possible.
- Progress stages are printed to Streamlit logs so the last active stage can be identified if the cloud worker restarts.

## Equation / LaTeX rule (v1.3.0)

- Every explicit equation, formula, inequality, or reaction equation uses the dual review format: \`(Actual equation) \\(LaTeX code\\)\`. Example: \`(E = mc²) \\(E = mc^{2}\\)\`.
- The generator prompt requires LaTeX, and deterministic validation/normalization prevents obvious raw equation relations from being exported outside LaTeX delimiters.
- Duplicate and grounding checks strip LaTeX markup before comparison so required math formatting does not create false duplicate/source flags.
- Existing quiz prose remains unchanged except for the equation representation itself.
- \`Match the following\` and \`Categorisation\` behavior is intentionally unchanged; \`Right Answer\` remains blank for these two types.



## Large-batch mode (v1.4.0)

- Supports uploads containing 50, 100, 500, or more lesson bundles, subject to the hosting service's runtime/memory limits and the OpenAI account's rate limits.
- Processes lessons concurrently with a configurable 1-6 worker pool; the Streamlit default is 4.
- OpenAI requests use a 180-second timeout and three SDK retries by default.
- File-role scoring is cached so each DOCX is content-sniffed only once instead of once per role.
- ZIP uploads are extracted from a disk-backed temporary archive instead of creating another full in-memory ZIP copy.
- The app no longer rebuilds the full result ZIP after every lesson. It builds checkpoint ZIP parts every 25 successful lessons and creates the full ZIP once at the end.
- A failed lesson does not stop the remaining lessons.
- For very large runs, checkpoint ZIP parts remain available in the current Streamlit session if the batch exits normally or hits a caught orchestration error.
