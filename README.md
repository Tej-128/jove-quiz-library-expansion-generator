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
7. Generates exactly 18 additional questions per lesson:
   - 3 Multi Correct
   - 3 True or False
   - 3 Fill in the Blanks
   - 3 Dropdown
   - 3 Match the following
   - 3 Categorisation
8. Prevents exact/near duplicates against both the existing quiz and the newly generated questions.
9. Randomizes answer/choice positions for generated choice-based formats while never shuffling existing Word questions.
10. Appends all 18 generated questions below the existing quiz rows.
11. Colors questions requiring manual review in Excel:
    - Yellow: manual review recommended
    - Red: critical parse/QA issue
12. Produces one Excel workbook per lesson plus a batch summary CSV inside the final ZIP.

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

Existing questions appear first, in original order. The 18 generated questions are appended directly below them. A second `QA Summary` sheet documents flags and source files.

## Streamlit deployment

1. Push this repository to GitHub.
2. Deploy `app.py` in Streamlit Community Cloud.
3. Add the secret:

```toml
OPENAI_API_KEY = "..."
```

4. Upload a chapter ZIP, enter the subject, review detected file roles, and generate.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```
