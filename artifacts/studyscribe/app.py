import os
import io
import json
import mimetypes
import time
import shutil
import subprocess
import tempfile
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
MAX_PDF_VISUAL_PAGES = 20
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
    "ogg",
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
اكتب المعادلات والصيغ الكيميائية بترميز LaTeX قياسي بين $...$ أو $$...$$. اعتبر الصورة الأصلية المرجع الأوثق للأسهم والرموز والمعادلات.

المادة:
""",
    "quiz": """أنت أستاذ جامعي يصمم اختبارًا للتعلّم النشط. اكتب الناتج بالعربية الطبيعية الواضحة للطلاب المصريين والعرب.
أنشئ 6 أسئلة اختيار من متعدد، لكل سؤال 4 خيارات فقط. لا تكتب الإجابة الصحيحة داخل السؤال أو الخيارات أو الشرح قبل اختيار الطالب.
أعد JSON يطابق المخطط المحدد: questions مصفوفة أسئلة؛ لكل سؤال question وoptions وcorrectIndex (رقم من 0 إلى 3) وexplanation.
استخدم المعلومات الموجودة في المادة فقط، واكتب بالعربية الطبيعية الواضحة. حافظ على الرموز العلمية واكتب المعادلات والصيغ الكيميائية بترميز LaTeX بين $...$ أو $$...$$.
استخدم الصورة الأصلية للتحقق من الأسهم والرموز، ولا تضف Markdown أو نصًا خارج JSON.

المادة:
""",
    "assignment": """أنت مساعد تدريس داعم. حوّل المادة إلى تكليف دراسي عملي باللغة العربية الطبيعية المناسبة للطلاب المصريين والعرب.
أضف عنوانًا، وأهداف التعلّم، و3 مهام تزداد صعوبتها، وسؤال تأمل قصير، وروبركًا مختصرًا. اجعل التكليف قابلًا للإنجاز خلال 45–60 دقيقة ولا تخترع معلومات. اكتب المعادلات والصيغ الكيميائية بترميز LaTeX قياسي بين $...$ أو $$...$$، وحافظ على الأسهم والرموز كما تظهر في المصدر.

المادة:
""",
    "notes": """أنت مساعد تدوين ممتاز. حوّل المادة إلى ملاحظات مذاكرة جاهزة للامتحان باللغة العربية الطبيعية المناسبة للطلاب المصريين والعرب.
استخدم نقاطًا متداخلة، ومصطلحات مهمة بخط عريض، وتعريفات قصيرة، وفي النهاية قسمًا بعنوان «أسئلة يجب أن أستطيع الإجابة عنها». حافظ على الدقة والتفاصيل والرموز ولا تضف ادعاءات غير موجودة. اكتب المعادلات والصيغ الكيميائية بترميز LaTeX قياسي بين $...$ أو $$...$$، ولا تستبدل الأسهم أو المثلثات أو الرموز العلمية بنص تقريبي.

المادة:
""",
}

QUIZ_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "correctIndex": {"type": "INTEGER"},
                    "explanation": {"type": "STRING"},
                },
                "required": ["question", "options", "correctIndex", "explanation"],
            },
        },
    },
    "required": ["questions"],
}


def error(message, status=400):
    return jsonify({"error": message}), status


def gemini_failure(exc, operation, fallback):
    app.logger.exception(operation)
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    details = str(exc).upper()
    if status_code == 503 or "503 UNAVAILABLE" in details:
        return error("Gemini is temporarily busy. Please try again in a moment.", 503)
    if status_code == 429 or "429 RESOURCE_EXHAUSTED" in details:
        return error("Gemini is temporarily rate-limited. Please try again shortly.", 429)
    return error(f"{fallback} ({type(exc).__name__})", 502)


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


def pdf_page_contains_images(page):
    try:
        resources = page.get("/Resources")
        parent = page.get("/Parent")
        while not resources and parent:
            parent = parent.get_object()
            resources = parent.get("/Resources")
            parent = parent.get("/Parent")
        if not resources:
            return False
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        xobjects = resources.get("/XObject")
        if not xobjects:
            return False
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        for reference in xobjects.values():
            xobject = reference.get_object() if hasattr(reference, "get_object") else reference
            if xobject.get("/Subtype") != "/Image":
                continue
            width = int(xobject.get("/Width", 0))
            height = int(xobject.get("/Height", 0))
            if width * height >= 100_000:
                return True
    except Exception:
        app.logger.info("Could not inspect PDF image resources", exc_info=True)
    return False


def extract_pdf_content(data):
    reader = PdfReader(io.BytesIO(data), strict=False)
    pages = []
    page_texts = {}
    visual_pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        page_texts[page_number] = text
        if text:
            pages.append(f"[Page {page_number}]\n{text}")
        if len(text) < MIN_EXTRACTED_TEXT or pdf_page_contains_images(page):
            visual_pages.append(page_number)
    return "\n\n".join(pages).strip(), page_texts, visual_pages


def extract_pdf_text(data):
    return extract_pdf_content(data)[0]


def render_pdf_pages(data, page_numbers):
    """Render selected PDF pages for OCR and vision instead of relying on PDF parsing."""
    if not page_numbers:
        return []
    if len(page_numbers) > MAX_PDF_VISUAL_PAGES:
        raise ValueError(
            f"هذا الـPDF يحتوي على أكثر من {MAX_PDF_VISUAL_PAGES} صفحة ممسوحة ضوئيًا. "
            "قسّمه إلى ملفات أصغر حتى نتمكن من قراءة كل صفحة بدقة."
        )
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError("PDF page rendering is unavailable on this server.")

    with tempfile.TemporaryDirectory(prefix="modaker-pdf-") as temp_dir:
        pdf_path = Path(temp_dir) / "source.pdf"
        output_prefix = Path(temp_dir) / "page"
        pdf_path.write_bytes(data)
        first_page, last_page = min(page_numbers), max(page_numbers)
        command = [
            renderer,
            "-f", str(first_page),
            "-l", str(last_page),
            "-r", "144",
            "-jpeg",
            "-jpegopt", "quality=88",
        ]
        if first_page == last_page:
            command.append("-singlefile")
        command.extend([str(pdf_path), str(output_prefix)])
        subprocess.run(command, check=True, capture_output=True, timeout=90)

        rendered = []
        for page_number in page_numbers:
            image_path = (
                output_prefix.with_suffix(".jpg")
                if first_page == last_page
                else Path(f"{output_prefix}-{page_number}.jpg")
            )
            if not image_path.is_file():
                raise RuntimeError(f"PDF renderer did not produce page {page_number}.")
            with Image.open(image_path) as image:
                image = image.convert("RGB")
                image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=88, optimize=True)
                rendered.append((page_number, buffer.getvalue()))
        return rendered


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


def parse_interactive_quiz(response_text):
    text = (response_text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini returned an invalid interactive quiz. Please try again.") from exc

    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list) or len(questions) != 6:
        raise RuntimeError("Gemini returned an incomplete quiz. Please try again.")

    normalized = []
    for question in questions:
        if not isinstance(question, dict):
            raise RuntimeError("Gemini returned an invalid quiz question. Please try again.")
        prompt_text = clean_text(question.get("question"))
        options = question.get("options")
        explanation = clean_text(question.get("explanation"))
        correct_index = question.get("correctIndex")
        if (
            not prompt_text
            or not isinstance(options, list)
            or len(options) != 4
            or any(not isinstance(option, str) or not option.strip() for option in options)
            or type(correct_index) is not int
            or correct_index < 0
            or correct_index >= len(options)
            or not explanation
        ):
            raise RuntimeError("Gemini returned an invalid quiz question. Please try again.")
        normalized.append({
            "question": prompt_text,
            "options": [option.strip() for option in options],
            "correctIndex": correct_index,
            "explanation": explanation,
        })
    return {"questions": normalized}


def quiz_to_markdown(quiz):
    lines = ["# Practice quiz", ""]
    letters = "ABCD"
    for index, question in enumerate(quiz["questions"], start=1):
        lines.extend([f"## Question {index}", question["question"], ""])
        lines.extend(
            f"{letters[option_index]}) {option}"
            for option_index, option in enumerate(question["options"])
        )
        lines.extend([
            "",
            f"Answer: {letters[question['correctIndex']]}) {question['options'][question['correctIndex']]}",
            f"Explanation: {question['explanation']}",
            "",
        ])
    return "\n".join(lines).strip()


def generate_study_material(api_key, action, prompt, attachments=None, inline_attachments=None):
    response_text = generate_text(
        api_key,
        prompt,
        attachments,
        inline_attachments,
        response_schema=QUIZ_RESPONSE_SCHEMA if action == "quiz" else None,
    )
    if action != "quiz":
        return response_text, None
    quiz = parse_interactive_quiz(response_text)
    return quiz_to_markdown(quiz), quiz


def generate_text(api_key, prompt, attachments=None, inline_attachments=None, response_schema=None):
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
        config = None
        if response_schema:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
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
        result, quiz = generate_study_material(api_key, action, PROMPTS[action] + content)
        payload = {"result": result, "action": action}
        if quiz is not None:
            payload["quiz"] = quiz
        return jsonify(payload)
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        return gemini_failure(
            exc,
            "Gemini text generation failed",
            "Gemini could not process this request. Check your key and try again.",
        )


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
            result, quiz = generate_study_material(api_key, action, PROMPTS[action] + text)
            payload = {"result": result, "action": action, "filenames": []}
            if quiz is not None:
                payload["quiz"] = quiz
            return jsonify(payload)
        except ValueError as exc:
            return error(str(exc))
        except Exception as exc:
            return gemini_failure(
                exc,
                "Gemini text generation failed from multipart input",
                "Gemini could not process this text. Check your key and try again.",
            )
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
            page_texts = {}
            visual_pages = []
            try:
                if extension == "pdf":
                    extracted_text, page_texts, visual_pages = extract_pdf_content(data)
                else:
                    extracted_text = extract_upload_text(data, extension)
            except Exception:
                app.logger.warning("Local extraction failed for %s; using Gemini binary fallback", filename, exc_info=True)
                extracted_text = ""
            if extracted_text:
                extracted_parts.append(f"[Source file: {filename}]\n{extracted_text}")
            if extension == "pdf" and visual_pages:
                try:
                    rendered_pages = render_pdf_pages(data, visual_pages)
                    for page_number, page_data in rendered_pages:
                        if len(page_texts.get(page_number, "")) < MIN_EXTRACTED_TEXT:
                            try:
                                page_ocr = extract_image_text(page_data)
                                if page_ocr:
                                    extracted_parts.append(
                                        f"[Scanned page {page_number} OCR: {filename}]\n{page_ocr}"
                                    )
                            except Exception:
                                app.logger.warning(
                                    "OCR failed for rendered PDF page %s from %s",
                                    page_number,
                                    filename,
                                    exc_info=True,
                                )
                        attachment = {
                            "data": page_data,
                            "mime_type": "image/jpeg",
                            "filename": f"{filename}-page-{page_number}.jpg",
                        }
                        if len(page_data) <= MAX_INLINE_IMAGE_SIZE:
                            inline_fallbacks.append(attachment)
                        else:
                            binary_fallbacks.append(attachment)
                except ValueError:
                    raise
                except Exception:
                    app.logger.warning(
                        "PDF page rendering failed for %s; using Gemini PDF fallback",
                        filename,
                        exc_info=True,
                    )
                    binary_fallbacks.append(
                        {"data": data, "mime_type": mime_type, "filename": filename}
                    )
            elif extension == "pdf" and len(extracted_text) < MIN_EXTRACTED_TEXT:
                binary_fallbacks.append(
                    {"data": data, "mime_type": mime_type, "filename": filename}
                )
            elif extension in IMAGE_EXTENSIONS:
                # OCR can flatten subscripts and lose arrows, diagrams, and
                # handwriting. Always send the original image alongside OCR.
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
والنص العربي كما هو. اكتب المعادلات والصيغ الكيميائية بترميز LaTeX قابل للعرض بين $...$ أو $$...$$.
إذا أُرفقت صور، استخدم الصورة الأصلية كمرجع أعلى أولوية لاستعادة المخططات والكتابة اليدوية والأسهم
والمثلثات والرموز وتنسيق الصفحة، ولا تعتمد على OCR وحده عند تعارضه مع الصورة.
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
        result, quiz = generate_study_material(
            api_key,
            action,
            prompt + ("\n\n" + all_context if all_context else ""),
            binary_fallbacks,
            inline_fallbacks,
        )
        payload = {"result": result, "action": action, "filenames": filenames}
        if quiz is not None:
            payload["quiz"] = quiz
        return jsonify(payload)
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:
        return gemini_failure(
            exc,
            "Gemini file processing failed",
            "تعذر على Gemini قراءة الملف. جرّب PDF أصغر أو صورة أوضح.",
        )


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
        if extension not in {"wav", "mp3", "m4a", "webm", "mp4", "ogg"}:
            return error("Use a WAV, MP3, M4A, WebM, MP4, or OGG recording for transcription.")
        mime_type = {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "m4a": "audio/mp4",
            "webm": "audio/webm",
            "mp4": "audio/mp4",
            "ogg": "audio/ogg",
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
        return gemini_failure(
            exc,
            "Gemini transcription failed",
            "Gemini could not transcribe this recording. Check the audio format and your key.",
        )


@app.errorhandler(413)
def request_too_large(_error):
    return error("Files are limited to 25 MB. Try a shorter recording or a compressed PDF.", 413)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )