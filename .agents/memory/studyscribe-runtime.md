---
name: StudyScribe runtime
description: Flask app runtime and routing constraints for the student study workspace.
---

StudyScribe runs as a Flask service inside the web artifact. Its browser-facing Flask endpoints use `/studyscribe-api/*` rather than `/api/*` because the workspace proxy reserves `/api` for the shared API service.

**Why:** The workspace already has a separate API artifact mounted at `/api`, so using that prefix caused preview requests to bypass Flask.

**How to apply:** Keep future StudyScribe client requests on `/studyscribe-api/*` unless the proxy layout changes.

The free allowance is intentionally browser-scoped: five credits are stored with the current local calendar date, each AI operation spends one, rewarded-ad simulation adds three, and a valid BYOK key bypasses both spending and the ad UI.

**Why:** The app has no user authentication or persistent account model, while the requested behavior is useful immediately without adding identity and database scope.

**How to apply:** If credits need anti-abuse guarantees or cross-device persistence later, add authenticated server-side accounting instead of expanding the localStorage model.