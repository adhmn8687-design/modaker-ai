import os
import io
import mimetypes
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types
from pypdf import PdfReader
from PIL import Image, ImageOps
import pytesseract
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 10
MIN_EXTRACTED_TEXT = 24
MAX_INLINE_IMAGE_SIZE = 4 * 1024 * 1024
MAX_INLINE_IMAGE_TOTAL = 12 * 1024 * 1024
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "heic", "heif"}
ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
    "heic",
    "heif",
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
    "summary": """أنت مُدرّس خصوصي دقيق وصبور للطلاب العرب، واكتب الناتج بالعربية الطبيعية الواضحة المناسبة للطلاب المصريين والعرب.
لخّص المادة دون اختلاق معلومات، وحافظ على المصطلحات العلمية والأسماء الإنجليزية عند الحاجة. استخدم هذا الهيكل:
## في جملة واحدة
## الأفكار الأساسية
## مصطلحات مهمة
## ماذا أراجع بعد ذلك

المادة:
""",
    "quiz": """أنت أستاذ جامعي يصمم اختبارًا للتعلّم النشط. اكتب الناتج بالعربية الطبيعية الواضحة للطلاب المصريين والعرب.
أنشئ 6 أسئلة متنوعة بين الاختيار من متعدد والإجابة القصيرة. اكتب الإجابة مباشرة بعد كل سؤال تحت عنوان «الإجابة»، ثم أضف شرحًا من جملة واحدة.
استخدم Markdown واضحًا، ولا تعتمد إلا على المعلومات الموجودة في المادة. حافظ على الرموز والمصطلحات العلمية كما هي.

المادة:
""",
    "assignment": """أنت مساعد تدريس داعم. حوّل المادة إلى تكليف دراسي عملي باللغة العربية الطبيعية المناسبة للطلاب المصريين والعرب.
أضف عنوانًا، وأهداف التعلّم، و3 مهام تزداد صعوبتها، وسؤال تأمل قصير، وروبركًا مختصرًا. اجعل التكليف قابلًا للإنجاز خلال 45–60 دقيقة ولا تخترع معلومات.

المادة:
""",
    "notes": """أنت مساعد تدوين ممتاز. حوّل المادة إلى ملاحظات مذاكرة جاهزة للامتحان باللغة العربية الطبيعية المناسبة للطلاب المصريين والعرب.
استخدم نقاطًا متداخلة، ومصطلحات مهمة بخط عريض، وتعريفات قصيرة، وفي النهاية قسمًا بعنوان «أسئلة يجب أن أستطيع الإجابة عنها». حافظ على الدقة والتفاصيل والرموز ولا تضف ادعاءات غير موجودة.

المادة:
""",
}


def error(message, status=400):
    return jsonify({"error": message}), status


def valid_api_key(value):
    return isinstance(value, str) and len(value.strip()) >= 10


def clean_text(value):
    return value.strip() if isinstance(value, str) else ""


def resolve_api_key(api_key=""):
    provided_key = api_key.strip() if isinstance(api_key, str) else ""
    return provided_key or os.environ.get("GEMINI_API_KEY", "").strip()


def get_client(api_key=""):
    resolved_key = resolve_api_key(api_key)
    if not valid_api_key(resolved_key):
        raise ValueError(
            "Modaker AI غير متصل بـ Gemini بعد. أضف مفتاحًا شخصيًا أو اضبط GEMINI_API_KEY."
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
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    image.load()
    try:
        return pytesseract.image_to_string(image, lang="ara+eng").strip()
    except pytesseract.TesseractError:
        # Keep extraction usable on runtimes where only the default language
        # pack is installed; the original image still goes to Gemini.
        return pytesseract.image_to_string(image).strip()


def extract_upload_text(data, extension):
    if extension == "pdf":
        return extract_pdf_text(data)
    if extension in IMAGE_EXTENSIONS:
        return extract_image_text(data)
    return ""


def normalize_image_mime(filename, data, supplied_mime=""):
    """Return a Gemini-safe image MIME type based on bytes first, then name."""
    extension = Path(filename or "").suffix.lower().lstrip(".")
    header = data[:32]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if b"ftyp" in data[:16] and any(
        brand in data[:32] for brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
    ):
        return "image/heic"
    if extension in {"jpg", "jpeg"}:
        return "image/jpeg"
    if extension == "png":
        return "image/png"
    if extension == "webp":
        return "image/webp"
    if extension == "gif":
        return "image/gif"
    if extension in {"heic", "heif"}:
        return "image/heic"
    if supplied_mime.startswith("image/"):
        return supplied_mime.split(";", 1)[0].lower()
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed if guessed and guessed.startswith("image/") else "application/octet-stream"


def attachment_part(attachment):
    """Build an SDK inlineData part without manually constructing base64."""
    return types.Part.from_bytes(
        data=attachment["data"],
        mime_type=attachment["mime_type"],
    )


def generate_text(api_key, prompt, attachments=None, inline_attachments=None):
    client = get_client(api_key)
    if isinstance(attachments, dict):
        attachments = [attachments]
    attachments = attachments or []
    inline_attachments = inline_attachments or []
    uploaded_files = []
    try:
        parts = [types.Part.from_text(text=prompt)]
        parts.extend(attachment_part(item) for item in inline_attachments)
        for attachment in attachments:
            stream = io.BytesIO(attachment["data"])
            upload_config = types.UploadFileConfig(
                mime_type=attachment["mime_type"],
                display_name=attachment.get("filename", "modaker-source"),
            )
            uploaded_file = client.files.upload(file=stream, config=upload_config)
            uploaded_file = wait_for_file(client, uploaded_file)
            uploaded_files.append(uploaded_file)
            file_uri = getattr(uploaded_file, "uri", None)
            file_mime = getattr(uploaded_file, "mime_type", None) or attachment["mime_type"]
            if not file_uri:
                raise RuntimeError("Gemini did not return a usable file URI.")
            parts.append(types.Part.from_uri(file_uri=file_uri, mime_type=file_mime))
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(role="user", parts=parts)],
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
    return jsonify({"status": "ok", "service": "modaker-ai"})


@app.post("/studyscribe-api/generate")
def generate():
    body = request.get_json(silent=True) or {}
    action = body.get("action", "summary")
    content = clean_text(body.get("text") if body.get("text") is not None else body.get("content"))
    api_key = body.get("apiKey") or body.get("api_key") or ""

    if action not in PROMPTS:
        return error("Choose a supported study action.")
    if len(content) < 20:
        return error("أضف نصًا واضحًا لا يقل عن بضعة أسطر حتى يتمكن Modaker AI من مساعدتك.")

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
    text = clean_text(request.form.get("text") if request.form.get("text") is not None else request.form.get("content"))
    uploads = request.files.getlist("files") or request.files.getlist("file")

    if action not in PROMPTS:
        return error("Choose a supported study action.")
    # Text mode is intentionally checked before request.files. This makes a
    # multipart request with no real file objects behave exactly like JSON
    # text mode and prevents empty-file validation or ClientError paths.
    if len(text) >= 20:
        try:
            result = generate_text(api_key, PROMPTS[action] + text)
            return jsonify({"result": result, "action": action, "filenames": []})
        except ValueError as exc:
            return error(str(exc))
        except Exception as exc:
            app.logger.exception("Gemini text generation failed from multipart input")
            return error(f"Gemini could not process this text. Check your key and try again. ({type(exc).__name__})", 502)
    if not uploads:
        return error("اكتب مادة دراسية أو اختر ملف PDF/صورة أولًا.")
    if len(uploads) > MAX_UPLOAD_COUNT:
        return error(f"Choose up to {MAX_UPLOAD_COUNT} files at a time.")

    try:
        extracted_parts = []
        binary_fallbacks = []
        inline_fallbacks = []
        filenames = []
        for upload in uploads:
            filename = secure_filename(upload.filename or "")
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if extension not in {"pdf", *IMAGE_EXTENSIONS}:
                return error(f"{filename or 'الملف'} يجب أن يكون PDF أو صورة.")
            data = upload.read()
            if not data:
                return error(f"{filename or 'الملف'} فارغ.")
            filenames.append(filename)
            mime_type = "application/pdf" if extension == "pdf" else normalize_image_mime(
                filename, data, upload.mimetype or ""
            )
            try:
                extracted_text = extract_upload_text(data, extension)
            except Exception:
                app.logger.warning("Local extraction failed for %s; using Gemini binary fallback", filename, exc_info=True)
                extracted_text = ""
            should_use_binary = len(extracted_text) < MIN_EXTRACTED_TEXT
            if extension == "pdf" and pdf_contains_images(data):
                should_use_binary = True
            if extracted_text:
                extracted_parts.append(f"[Source file: {filename}]\n{extracted_text}")
            if should_use_binary:
                attachment = {"data": data, "mime_type": mime_type, "filename": filename}
                if mime_type.startswith("image/") and len(data) <= MAX_INLINE_IMAGE_SIZE:
                    inline_fallbacks.append(attachment)
                else:
                    binary_fallbacks.append(attachment)

        prompt = (
            PROMPTS[action]
            + """
النص التالي استُخرج محليًا من ملف أو أكثر.
اكتب الناتج بالعربية الواضحة، مع الحفاظ على التفاصيل التقنية والمعادلات والرموز والصيغ الكيميائية
والنص العربي كما هو. إذا أُرفقت صور، استخدمها لاستعادة المخططات والكتابة اليدوية وتنسيق الصفحة.
ادمج كل الملفات في سياق دراسي واحد ولا تكرر المعلومات بلا داعٍ.
"""
        )
        all_context = "\n\n".join(extracted_parts).strip()
        if len(all_context) < MIN_EXTRACTED_TEXT and not binary_fallbacks:
            if not inline_fallbacks:
                return error("تعذر استخراج نص مقروء من الملفات. جرّب صورًا أوضح أو ملف PDF أصغر.")
        if sum(len(item["data"]) for item in inline_fallbacks) > MAX_INLINE_IMAGE_TOTAL:
            binary_fallbacks.extend(inline_fallbacks)
            inline_fallbacks = []
        result = generate_text(
            api_key,
            prompt + ("\n\n" + all_context if all_context else ""),
            binary_fallbacks,
            inline_fallbacks,
        )
        return jsonify({"result": result, "action": action, "filenames": filenames})
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        app.logger.exception("Gemini file processing failed")
        return error(f"تعذر على Gemini قراءة الملف. جرّب PDF أصغر أو صورة أوضح. ({type(exc).__name__})", 502)


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
        prompt = """فرّغ هذا التسجيل الصوتي بدقة.
أعد النص فقط، مع فقرات عند انتقال المتحدث أو الموضوع. حافظ على العربية والمصطلحات العلمية والإنجليزية،
ولا تلخّص ولا تضف تعليقًا."""
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