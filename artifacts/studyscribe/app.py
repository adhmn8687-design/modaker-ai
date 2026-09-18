import os
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


def get_client(api_key):
    if not valid_api_key(api_key):
        raise ValueError("Add your Gemini API key before generating study material.")
    return genai.Client(api_key=api_key.strip())


def generate_text(api_key, prompt, attachment=None):
    client = get_client(api_key)
    contents = [prompt]
    if attachment:
        contents.append(
            types.Part.from_bytes(
                data=attachment["data"],
                mime_type=attachment["mime_type"],
            )
        )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
    )
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
        mime_type = upload.mimetype or (
            "application/pdf" if extension == "pdf" else f"image/{'jpeg' if extension == 'jpg' else extension}"
        )
        prompt = (
            PROMPTS[action]
            + "\nThe attached file is the source material. If it is an image, read visible text and diagrams carefully."
        )
        result = generate_text(api_key, prompt, {"data": data, "mime_type": mime_type})
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
        transcript = generate_text(api_key, prompt, {"data": data, "mime_type": mime_type})
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