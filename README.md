# JoVE Quiz Library Expansion Generator

Current production build: v1.7.0_local_resumable_21q

This build runs production batches locally through Streamlit so each completed lesson workbook is written directly to a Windows folder and is not dependent on a hosted Streamlit session.

## Locked quiz behavior

- Existing Word wording and option order remain preserved.
- The existing * marker defines correct answers.
- Source grounding remains PageText plus Transcript or CC only.
- Exactly 21 new questions are generated per lesson: 3 each of Single Correct, Multi Correct, True or False, Fill in the Blanks, Dropdown, Match the following, and Categorisation.
- Match the following and Categorisation keep the established JoVE structure and blank Right Answer behavior.
- Equations keep the required Actual Equation followed by LaTeX representation.
- One JoVE-red separator row appears between existing and newly generated questions.
- Yellow/red QA flags and the automated QA-repair behavior remain enabled.
- One Excel workbook is produced per lesson.

## Windows production run

1. Download or clone this repository.
2. Double-click RUN_LOCAL_WINDOWS.bat.
3. The local Streamlit UI opens on http://127.0.0.1:8501.
4. Paste the OpenAI API key if it is not already configured locally.
5. Choose the Output folder.
6. Upload one or more chapter ZIPs.
7. Start the local batch.

## Large-batch resilience

The local runtime is designed for 50 to 500+ lesson batches.

- Recommended starting concurrency: 12 lesson workers.
- Selectable concurrency: 4 to 16.
- Concurrency automatically reduces if API rate limiting is detected.
- Every lesson gets up to three worker-level attempts in addition to existing OpenAI request retries.
- Each lesson runs in its own temporary work directory.
- A workbook is validated before it becomes visible in the output folder.
- Every successfully completed lesson workbook is moved into the output folder immediately.
- generation_progress_JOBID.csv is updated after every completion or failure.
- Job state and copied input files are persisted under .jove_jobs inside the output folder.
- A detached supervisor restarts the worker after an unexpected Python-process crash.
- Resume/retry skips completed lesson workbooks and works only on unfinished lessons.
- Browser or Streamlit page refresh does not delete already completed outputs.

No source documents, API keys, or generated quiz files are committed to GitHub.

## Expected input

The expected per-lesson structure remains:

Chapter Folder / Lesson ID / PageText + Transcript or CC + Existing Quiz Word document

Exact filenames are not required. Existing fuzzy role detection remains unchanged.

## Excel output schema

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
