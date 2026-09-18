import os
import io
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types
from pypdf import PdfReader
from PIL import Image
import pytesseract
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 10
MIN_EXTRACTED_TEXT = 24
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


def pdf_contains_images(data):
    try:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            resources = page.get("/Resources")
            if not resources:
                continue
            xobjects = resources.get("/XObject")
            if not xobjects:
                continue
            for reference in xobjects.values():
                xobject = reference.get_object()
                if xobject.get("/Subtype") == "/Image":
                    return True
    except Exception:
        app.logger.info("Could not inspect PDF image resources", exc_info=True)
    return False


def extract_pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {page_number}]\n{text}")
    return "\n\n".join(pages).strip()


def extract_image_text(data):
    image = Image.open(io.BytesIO(data))
    image.load()
    return pytesseract.image_to_string(image).strip()


def extract_upload_text(data, extension):
    if extension == "pdf":
        return extract_pdf_text(data)
    if extension in {"png", "jpg", "jpeg", "webp", "gif"}:
        return extract_image_text(data)
    return ""


def generate_text(api_key, prompt, attachments=None):
    client = get_client(api_key)
    if isinstance(attachments, dict):
        attachments = [attachments]
    attachments = attachments or []
    if not attachments:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
        )
    else:
        uploaded_files = []
        try:
            for attachment in attachments:
                stream = io.BytesIO(attachment["data"])
                upload_config = types.UploadFileConfig(
                    mime_type=attachment["mime_type"],
                    display_name=attachment.get("filename", "studyscribe-source"),
                )
                uploaded_file = client.files.upload(file=stream, config=upload_config)
                uploaded_files.append(uploaded_file)
                uploaded_files[-1] = wait_for_file(client, uploaded_file)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, *uploaded_files],
            )
        finally:
            for uploaded_file in uploaded_files:
                try:
                    if getattr(uploaded_file, "name", None):
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
    api_key = body.get("apiKey") or body.get("api_key") or ""

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
    api_key = request.form.get("apiKey") or request.form.get("api_key") or ""
    action = request.form.get("action", "summary")
    uploads = request.files.getlist("files") or request.files.getlist("file")
    source_context = request.form.get("content", "").strip()

    if action not in PROMPTS:
        return error("Choose a supported study action.")
    if not uploads:
        return error("Choose a PDF or image to process.")
    if len(uploads) > MAX_UPLOAD_COUNT:
        return error(f"Choose up to {MAX_UPLOAD_COUNT} files at a time.")

    try:
        extracted_parts = []
        binary_fallbacks = []
        filenames = []
        for upload in uploads:
            filename = secure_filename(upload.filename or "")
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if extension not in {"pdf", "png", "jpg", "jpeg", "webp", "gif"}:
                return error(f"{filename or 'One upload'} needs to be a PDF or an image.")
            data = upload.read()
            if not data:
                return error(f"{filename or 'One upload'} is empty.")
            filenames.append(filename)
            try:
                extracted_text = extract_upload_text(data, extension)
            except Exception:
                app.logger.warning("Local extraction failed for %s; using Gemini Files API fallback", filename, exc_info=True)
                extracted_text = ""
            should_use_binary = len(extracted_text) < MIN_EXTRACTED_TEXT
            if extension == "pdf" and pdf_contains_images(data):
                should_use_binary = True
            if extracted_text:
                extracted_parts.append(f"[Source file: {filename}]\n{extracted_text}")
            if should_use_binary:
                mime_type = "application/pdf" if extension == "pdf" else f"image/{'jpeg' if extension in {'jpg', 'jpeg'} else extension}"
                binary_fallbacks.append(
                    {"data": data, "mime_type": mime_type, "filename": filename}
                )

        prompt = (
            PROMPTS[action]
            + """
The source text below was extracted locally from one or more uploaded files.
Preserve technical details, equations, subscripts, superscripts, symbols, and chemical formulas.
If binary source files are also attached, use them to recover diagrams, handwriting, layout, or
content that local extraction could not read. Combine all source files into one coherent study context.
"""
        )
        all_context = "\n\n".join(part for part in [source_context, *extracted_parts] if part).strip()
        if len(all_context) < MIN_EXTRACTED_TEXT and not binary_fallbacks:
            return error("Could not extract readable text from these files.")
        result = generate_text(
            api_key,
            prompt + "\n\n" + all_context if all_context else prompt,
            binary_fallbacks,
        )
        return jsonify({"result": result, "action": action, "filenames": filenames})
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        app.logger.exception("Gemini file processing failed")
        return error(f"Gemini could not read that file. Try a smaller PDF or clearer image. ({type(exc).__name__})", 502)


@app.post("/studyscribe-api/transcribe")
def transcribe():
    api_key = request.form.get("apiKey") or request.form.get("api_key") or ""
    upload = request.files.get("audio")
    if not upload or not upload.filename:
        return error("No recording was received.")

    data = upload.read()
    if not data:
        return error("The recording is empty.")
    try:
        extension = secure_filename(upload.filename).rsplit(".", 1)[-1].lower()
        if extension not in {"wav", "mp3", "m4a", "webm", "mp4"}:
            return error("Use a WAV or MP3 recording for transcription.")
        mime_type = {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
            "mp4": "audio/mp4",
        }[extension]
        prompt = """Transcribe this lecture recording accurately.
Return only the transcript, with paragraph breaks when the speaker changes topics. Preserve technical terms and do not summarize or add commentary."""
        transcript = generate_text(
            api_key,
            prompt,
            [{"data": data, "mime_type": mime_type, "filename": upload.filename}],
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