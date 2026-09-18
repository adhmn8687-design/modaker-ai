import os
import io
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
MAX_FILE_SIZE = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
    "mp3",
    "wav",
    "m4a",
    "webm",
    "mp4",
}

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


PROMPTS = {
    "summary": """You are a patient, precise study coach. Summarize the material below for a university student.
Use this structure:
## In one sentence
## Core ideas
## Terms to remember
## What to review next
Keep the response concise but useful. Do not invent facts.

Material:
""",
    "quiz": """You are an expert professor creating a retrieval-practice quiz from the material below.
Create 6 questions with a mix of multiple choice and short answer. Include the answer immediately after each question under an "Answer" label, plus a one-sentence explanation.
Use clear Markdown and only use information present in the material.

Material:
""",
    "assignment": """You are a supportive teaching assistant. Turn the material below into a focused study assignment.
Include: a title, learning goals, 3 tasks that build in difficulty, a small reflection prompt, and a compact rubric. Make it possible to complete in 45–60 minutes.

Material:
""",
    "notes": """You are an exceptional note-taking assistant. Convert the material below into exam-ready study notes.
Use nested bullets, bold key terms, short definitions, and a final "Questions I should be able to answer" section. Preserve nuance and do not add unsupported claims.

Material:
""",
}


def error(message, status=400):
    return jsonify({"error": message}), status


def valid_api_key(value):
    return isinstance(value, str) and len(value.strip()) >= 10


def resolve_api_key(api_key=""):
    provided_key = api_key.strip() if isinstance(api_key, str) else ""
    return provided_key or os.environ.get("GEMINI_API_KEY", "").strip()


def get_client(api_key=""):
    resolved_key = resolve_api_key(api_key)
    if not valid_api_key(resolved_key):
        raise ValueError(
            "StudyScribe is not connected to Gemini yet. Add a personal key or configure GEMINI_API_KEY."
        )
    return genai.Client(api_key=resolved_key)


def file_state_value(file):
    state = getattr(file, "state", None)
    return getattr(state, "value", str(state or "")).upper()


def wait_for_file(client, file):
    if file_state_value(file) in {"", "ACTIVE"}:
        return file
    for _ in range(60):
        if file_state_value(file) == "FAILED":
            details = getattr(file, "error", None)
            raise RuntimeError(f"Gemini could not process this file. {details or 'The file processor rejected it.'}")
        if file_state_value(file) == "ACTIVE":
            return file
        time.sleep(0.5)
        file = client.files.get(name=file.name)
    raise TimeoutError("Gemini took too long to finish processing this file. Try a smaller document.")


def generate_text(api_key, prompt, attachment=None):
    client = get_client(api_key)
    if not attachment:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
        )
    else:
        uploaded_file = None
        try:
            stream = io.BytesIO(attachment["data"])
            upload_config = types.UploadFileConfig(
                mime_type=attachment["mime_type"],
                display_name=attachment.get("filename", "studyscribe-source"),
            )
            uploaded_file = wait_for_file(
                client,
                client.files.upload(file=stream, config=upload_config),
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, uploaded_file],
            )
        finally:
            if uploaded_file and getattr(uploaded_file, "name", None):
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    app.logger.warning("Gemini file cleanup failed", exc_info=True)
    if not response.text:
        raise RuntimeError("Gemini returned an empty response. Try again with more material.")
    return response.text.strip()


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/studyscribe-healthz")
def healthz():
    return jsonify({"status": "ok", "service": "studyscribe"})


@app.post("/studyscribe-api/generate")
def generate():
    body = request.get_json(silent=True) or {}
    action = body.get("action", "summary")
    content = (body.get("content") or "").strip()
    api_key = body.get("api_key", "")

    if action not in PROMPTS:
        return error("Choose a supported study action.")
    if len(content) < 20:
        return error("Add at least a few sentences so StudyScribe has enough context.")

    try:
        result = generate_text(api_key, PROMPTS[action] + content)
        return jsonify({"result": result, "action": action})
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        app.logger.exception("Gemini text generation failed")
        return error(f"Gemini could not process this request. Check your key and try again. ({type(exc).__name__})", 502)


@app.post("/studyscribe-api/analyze-file")
def analyze_file():
    api_key = request.form.get("api_key", "")
    action = request.form.get("action", "summary")
    upload = request.files.get("file")

    if action not in PROMPTS:
        return error("Choose a supported study action.")
    if not upload or not upload.filename:
        return error("Choose a PDF or image to process.")
    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_EXTENSIONS or extension in {"mp3", "wav", "m4a", "webm", "mp4"}:
        return error("This upload needs to be a PDF or an image.")

    data = upload.read()
    if not data:
        return error("The selected file is empty.")
    try:
        mime_type = (
            "application/pdf"
            if extension == "pdf"
            else f"image/{'jpeg' if extension in {'jpg', 'jpeg'} else extension}"
        )
        prompt = (
            PROMPTS[action]
            + """
The attached source may be a scanned or image-heavy PDF. Use Gemini vision OCR page by page.
Read visible text, diagrams, equations, subscripts, superscripts, symbols, and chemical formulas
carefully. Preserve the meaning of handwritten or photographed notes, and do not skip pages
just because they contain little machine-readable text.
"""
        )
        result = generate_text(
            api_key,
            prompt,
            {"data": data, "mime_type": mime_type, "filename": filename},
        )
        return jsonify({"result": result, "action": action, "filename": filename})
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        app.logger.exception("Gemini file processing failed")
        return error(f"Gemini could not read that file. Try a smaller PDF or clearer image. ({type(exc).__name__})", 502)


@app.post("/studyscribe-api/transcribe")
def transcribe():
    api_key = request.form.get("api_key", "")
    upload = request.files.get("audio")
    if not upload or not upload.filename:
        return error("No recording was received.")

    data = upload.read()
    if not data:
        return error("The recording is empty.")
    try:
        mime_type = upload.mimetype or "audio/webm"
        prompt = """Transcribe this lecture recording accurately.
Return only the transcript, with paragraph breaks when the speaker changes topics. Preserve technical terms and do not summarize or add commentary."""
        transcript = generate_text(
            api_key,
            prompt,
            {"data": data, "mime_type": mime_type, "filename": upload.filename},
        )
        return jsonify({"transcript": transcript})
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        app.logger.exception("Gemini transcription failed")
        return error(f"Gemini could not transcribe this recording. Check the audio format and your key. ({type(exc).__name__})", 502)


@app.errorhandler(413)
def request_too_large(_error):
    return error("Files are limited to 25 MB. Try a shorter recording or a compressed PDF.", 413)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )