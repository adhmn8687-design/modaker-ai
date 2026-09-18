---
name: StudyScribe runtime
description: Flask app runtime and routing constraints for the student study workspace.
---

StudyScribe runs as a Flask service inside the web artifact. Its browser-facing Flask endpoints use `/studyscribe-api/*` rather than `/api/*` because the workspace proxy reserves `/api` for the shared API service.

**Why:** The workspace already has a separate API artifact mounted at `/api`, so using that prefix caused preview requests to bypass Flask.

**How to apply:** Keep future StudyScribe client requests on `/studyscribe-api/*` unless the proxy layout changes.

The free allowance is intentionally browser-scoped: five credits are stored with the current local calendar date, each AI operation spends one, rewarded-ad simulation adds three, and a valid BYOK key bypasses both spending and the ad UI. Sessions without BYOK use the server's `GEMINI_API_KEY` secret.

**Why:** The app has no user authentication or persistent account model, while the requested behavior is useful immediately without adding identity and database scope.

**How to apply:** If credits need anti-abuse guarantees or cross-device persistence later, add authenticated server-side accounting instead of expanding the localStorage model.

Gemini file inputs use the Files API with processing-state polling and cleanup rather than inline byte parts. PDF prompts explicitly request page-by-page vision OCR for scanned, image-heavy, and chemistry material.

**Why:** Inline PDF parts were the source of client errors and could under-read documents whose content was primarily scanned imagery.

**How to apply:** Preserve the upload → wait for ACTIVE → generate → delete lifecycle when changing file formats or prompts.