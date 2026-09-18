# StudyScribe

StudyScribe is a Flask-powered student workspace that turns text, lectures, PDFs, and images into focused study material with Google Gemini.

## Run & Operate

- `pnpm --filter @workspace/studyscribe run dev` — run the StudyScribe Flask app
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- The optional user credential is a BYOK Gemini API key entered in the browser. StudyScribe keeps it in `sessionStorage` and sends it only with the current request; default sessions use the `GEMINI_API_KEY` Replit Secret on Flask.

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- App: Python 3.11 + Flask
- AI: Google Gemini via the modern `google-genai` SDK
- UI: server-rendered Flask templates, Bootstrap Icons, and custom responsive CSS
- Uploads: in-memory request processing for PDFs, images, and audio; no user files are persisted

## Where things live

- `artifacts/studyscribe/app.py` — Flask routes, Gemini client, prompts, and upload handling
- `artifacts/studyscribe/templates/index.html` — StudyScribe workspace shell
- `artifacts/studyscribe/static/app.js` — browser interactions, recording, upload, and API calls
- `artifacts/studyscribe/static/styles.css` — responsive visual system
- `requirements.txt` — Python runtime dependencies

## Architecture decisions

- BYOK is request-scoped: the browser holds the key only in `sessionStorage`, and the Flask app does not save it.
- PDFs and images are extracted locally first with pypdf and Tesseract OCR; files with sparse or image-heavy content fall back to the modern Gemini Files API. Audio is converted to WAV in the browser before transcription.
- The browser records audio with `MediaRecorder`, then posts the resulting WebM blob to Flask for transcription.
- Uploaded bytes are processed in memory with a 25 MB limit; there is no file library or database to clean up.

## Product

- Paste lecture notes or textbook material and generate a summary, study notes, quiz, or assignment.
- Upload a PDF or image and use its contents as the source for any study format.
- Select multiple PDFs or screenshots at once; extracted content is combined into one study context.
- Record a lecture in the browser, transcribe it with Gemini, and place the transcript into the study composer.
- Copy or download generated Markdown, with a lightweight recent-output list for the current session.
- Five free AI credits reset daily, with a simulated rewarded ad that grants three more credits.
- A provided Gemini key switches the workspace to unlimited mode and removes the ad control.

## User preferences

No additional preferences recorded.

- StudyScribe uses `/studyscribe-api/*` instead of `/api/*` because the workspace already routes `/api` to the shared API service.
- Audio recording requires a browser permission and a secure browser context.
- Mobile recording failures open device-specific permission guidance for Android Chrome and iPhone/iPad Safari.
- Gemini failures are surfaced as explicit error messages; there is no silent fallback or mocked generation.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
