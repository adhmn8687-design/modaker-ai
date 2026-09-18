---
name: StudyScribe runtime
description: Flask app runtime and routing constraints for the student study workspace.
---

StudyScribe runs as a Flask service inside the web artifact. Its browser-facing Flask endpoints use `/studyscribe-api/*` rather than `/api/*` because the workspace proxy reserves `/api` for the shared API service.

**Why:** The workspace already has a separate API artifact mounted at `/api`, so using that prefix caused preview requests to bypass Flask.

**How to apply:** Keep future StudyScribe client requests on `/studyscribe-api/*` unless the proxy layout changes.