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

PDF and image inputs are extracted locally first with pypdf and Tesseract OCR. Sparse or image-heavy inputs then use the Gemini Files API with processing-state polling and cleanup rather than being passed as inline byte parts. The browser converts recordings to mono WAV before the audio route.

**Why:** Inline PDF parts were the source of client errors and could under-read documents whose content was primarily scanned imagery; local extraction makes text-first behavior deterministic while the Files API preserves diagrams and handwriting.

**How to apply:** Preserve the extract-first → combine all file context → Files API fallback lifecycle, and keep the upload → wait for ACTIVE → generate → delete lifecycle for binary fallbacks.

Text submissions must be routed before multipart file validation. Small normalized images may be sent as SDK-generated `inlineData` parts, while PDFs and oversized binaries should remain Files API uploads.

**Why:** Stale browser file selections and manually shaped binary payloads were causing Gemini ClientError responses, while large inline image batches risk request-size failures.

**How to apply:** Treat a valid `text` field as a file-free request, normalize image bytes to canonical MIME types, cap total inline image bytes, and use URI parts for Files API fallbacks.