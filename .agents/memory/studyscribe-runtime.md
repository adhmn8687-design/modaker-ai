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

PDF and image inputs are extracted locally first with pypdf and Tesseract OCR. Always send original raster images alongside OCR text because OCR can flatten equation subscripts, arrows, diagrams, and handwriting; small images use inline parts and larger binaries use the Files API. Browser recordings are converted to mono WAV when possible, with native-format fallback when decoding fails.

**Why:** OCR can return readable prose while silently corrupting the scientific symbols users need to study, so extracted-text length is not a reliable signal that the original image is unnecessary. Browser audio decoders also vary by format.

**How to apply:** Preserve locally extracted text as context, always retain raster sources for Gemini vision, cap aggregate inline image size, and keep the upload → wait for ACTIVE → generate → delete lifecycle for Files API fallbacks. Accept WAV and common browser-native audio types.

Text submissions must be routed before multipart file validation. Normalize and cap inline image parts, keep PDFs and oversized binaries on the Files API, and render generated LaTeX/chemical notation in the browser with MathJax plus mhchem.

**Why:** Stale browser file selections and manually shaped binary payloads caused Gemini ClientError responses; unrendered LaTeX appeared as garbled raw text even when the model returned correct formula syntax.

**How to apply:** Treat a valid `text` field as a file-free request; preserve original image bytes next to OCR; render inline `$...$`, display `$$...$$`, and `\ce{...}` math after result insertion while keeping copied/downloaded Markdown unchanged.

The Gemini model name must remain configurable through `GEMINI_MODEL`; the default is `gemini-3.6-flash` because `gemini-2.5-flash` returned a 404 for new users.

**Why:** The observed image ClientError occurred at `models.generate_content`, after valid image payload construction; the provider explicitly reported the old model was unavailable, so changing MIME handling alone could not fix it.

**How to apply:** When Gemini returns a model-availability 404, inspect the provider message before changing file payloads, update the configured model, restart Flask, and verify both text and multi-image routes with live HTTP requests.