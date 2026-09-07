"""PDF와 계약서 촬영 이미지에서 로컬로 텍스트를 추출한다."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from PIL import Image, ImageOps, UnidentifiedImageError

from .extraction_models import ExtractionResult, PageExtraction


MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_PAGE_COUNT = 30
MAX_IMAGE_PIXELS = 40_000_000
MIN_TEXT_CHARACTERS = 80
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_FORMATS = {"JPEG", "PNG"}


class DocumentValidationError(ValueError):
    """사용자가 수정할 수 있는 업로드 문서 오류."""


class OcrUnavailableError(RuntimeError):
    """OCR 실행 환경을 찾지 못했거나 필요한 언어가 없는 경우."""


@dataclass(frozen=True)
class PdfInspection:
    page_count: int
    embedded_texts: tuple[str, ...]


def validate_pdf(filename: str, data: bytes) -> None:
    if not filename.lower().endswith(".pdf"):
        raise DocumentValidationError("PDF 파일만 업로드할 수 있습니다.")
    if not data:
        raise DocumentValidationError("업로드한 파일이 비어 있습니다.")
    if len(data) > MAX_FILE_SIZE:
        raise DocumentValidationError("파일 크기는 20MB 이하여야 합니다.")
    if not data.lstrip().startswith(b"%PDF"):
        raise DocumentValidationError("PDF 형식을 확인할 수 없습니다.")


def _validate_file_size(data: bytes) -> None:
    if not data:
        raise DocumentValidationError("업로드한 파일이 비어 있습니다.")
    if len(data) > MAX_FILE_SIZE:
        raise DocumentValidationError("파일 크기는 20MB 이하여야 합니다.")


def _normalized_image_bytes(filename: str, data: bytes) -> bytes:
    """촬영 이미지의 실제 형식과 크기를 검증하고 회전을 보정해 PNG로 반환한다."""

    extension = Path(filename).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise DocumentValidationError("계약서는 PDF, JPG, JPEG, PNG 파일만 업로드할 수 있습니다.")
    _validate_file_size(data)

    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in IMAGE_FORMATS:
                raise DocumentValidationError("JPG 또는 PNG 이미지 형식을 확인할 수 없습니다.")
            expected_format = "PNG" if extension == ".png" else "JPEG"
            if source.format != expected_format:
                raise DocumentValidationError("파일 확장자와 실제 이미지 형식이 일치하지 않습니다.")
            width, height = source.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise DocumentValidationError("이미지 해상도는 4천만 픽셀 이하여야 합니다.")
            source.verify()

        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except DocumentValidationError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise DocumentValidationError("손상되었거나 지원하지 않는 이미지입니다.") from error


def inspect_pdf(data: bytes) -> PdfInspection:
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            if not document.pages:
                raise DocumentValidationError("페이지가 없는 PDF입니다.")
            if len(document.pages) > MAX_PAGE_COUNT:
                raise DocumentValidationError(f"PDF는 {MAX_PAGE_COUNT}페이지 이하여야 합니다.")
            texts = tuple((page.extract_text() or "").strip() for page in document.pages)
            return PdfInspection(page_count=len(document.pages), embedded_texts=texts)
    except DocumentValidationError:
        raise
    except Exception as error:
        raise DocumentValidationError("암호화되었거나 손상된 PDF는 처리할 수 없습니다.") from error


def _meaningful_character_count(text: str) -> int:
    return sum(character.isalnum() for character in text)


def find_tesseract() -> str | None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)

    executable = shutil.which("tesseract")
    if executable:
        return executable

    candidates = []
    for variable in ("PROGRAMFILES", "LOCALAPPDATA"):
        root = os.getenv(variable)
        if root:
            candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
            candidates.append(Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    candidates.extend(
        Path(path)
        for path in (
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/usr/bin/tesseract",
        )
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _available_languages(executable: str) -> set[str]:
    completed = subprocess.run(
        [executable, "--list-langs"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return {line.strip() for line in completed.stdout.splitlines()[1:] if line.strip()}


def _ocr_language(executable: str) -> str:
    languages = _available_languages(executable)
    if "kor" not in languages:
        raise OcrUnavailableError("Tesseract 한국어 언어 데이터(kor)가 필요합니다.")
    return "kor+eng" if "eng" in languages else "kor"


def _render_pages(data: bytes, page_indexes: list[int], dpi: int) -> dict[int, bytes]:
    """PDFium 접근을 호출 스레드에 한정해 페이지를 PNG 바이트로 렌더링한다."""

    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise OcrUnavailableError("스캔 PDF 처리를 위해 pypdfium2가 필요합니다.") from error

    document = pdfium.PdfDocument(data)
    try:
        rendered_pages: dict[int, bytes] = {}
        for page_index in page_indexes:
            page = document[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil().convert("RGB")
                image_bytes = io.BytesIO()
                image.save(image_bytes, format="PNG")
                rendered_pages[page_index] = image_bytes.getvalue()
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        return rendered_pages
    finally:
        document.close()


def _ocr_image(image_bytes: bytes, executable: str, language: str) -> str:
    """이미 렌더링된 이미지에 Tesseract subprocess만 실행한다."""

    completed = subprocess.run(
        [executable, "stdin", "stdout", "-l", language, "--psm", "6"],
        input=image_bytes,
        capture_output=True,
        timeout=120,
        check=True,
    )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def extract_pdf_text(
    filename: str,
    data: bytes,
    *,
    dpi: int = 250,
    max_workers: int = 2,
) -> ExtractionResult:
    """문서 텍스트를 추출한다. 충분한 텍스트 레이어는 OCR하지 않는다."""

    started = time.perf_counter()
    validate_pdf(filename, data)
    inspection = inspect_pdf(data)
    sparse_pages = [
        index
        for index, text in enumerate(inspection.embedded_texts)
        if _meaningful_character_count(text) < MIN_TEXT_CHARACTERS
    ]
    ocr_results: dict[int, str] = {}
    warnings: list[str] = []

    if sparse_pages:
        executable = find_tesseract()
        if not executable:
            warnings.append(
                "텍스트가 부족한 페이지가 있지만 Tesseract를 찾지 못해 OCR하지 못했습니다."
            )
        else:
            try:
                language = _ocr_language(executable)
                rendered_pages = _render_pages(data, sparse_pages, dpi)
                worker_count = max(1, min(max_workers, len(sparse_pages)))
                with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="registry-ocr") as executor:
                    futures = {
                        executor.submit(_ocr_image, rendered_pages[index], executable, language): index
                        for index in sparse_pages
                    }
                    for future in as_completed(futures):
                        index = futures[future]
                        try:
                            ocr_results[index] = future.result()
                        except (OcrUnavailableError, subprocess.SubprocessError, TimeoutError) as error:
                            warnings.append(f"{index + 1}페이지 OCR 실패: {error}")
            except (OcrUnavailableError, subprocess.SubprocessError) as error:
                warnings.append(str(error))

    pages = []
    for index, embedded_text in enumerate(inspection.embedded_texts):
        ocr_text = ocr_results.get(index, "").strip()
        if ocr_text and _meaningful_character_count(ocr_text) >= _meaningful_character_count(embedded_text):
            text, method = ocr_text, "tesseract"
        elif embedded_text:
            text, method = embedded_text, "embedded_text"
        else:
            text, method = "", "unreadable"
        pages.append(
            PageExtraction(
                page_number=index + 1,
                text=text,
                method=method,
                character_count=_meaningful_character_count(text),
            )
        )

    return ExtractionResult(
        pages=tuple(pages),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        warnings=tuple(warnings),
    )


def extract_image_text(filename: str, data: bytes) -> ExtractionResult:
    """JPG·JPEG·PNG 촬영본을 보정한 뒤 한 페이지로 OCR한다."""

    started = time.perf_counter()
    image_bytes = _normalized_image_bytes(filename, data)
    executable = find_tesseract()
    warnings: list[str] = []
    text = ""

    if not executable:
        warnings.append("계약서 이미지 OCR에 필요한 Tesseract를 찾지 못했습니다.")
    else:
        try:
            language = _ocr_language(executable)
            text = _ocr_image(image_bytes, executable, language)
        except (OcrUnavailableError, subprocess.SubprocessError, TimeoutError) as error:
            warnings.append(f"계약서 이미지 OCR 실패: {error}")

    method = "tesseract" if text else "unreadable"
    page = PageExtraction(
        page_number=1,
        text=text,
        method=method,
        character_count=_meaningful_character_count(text),
    )
    return ExtractionResult(
        pages=(page,),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        warnings=tuple(warnings),
    )


def extract_document_text(filename: str, data: bytes) -> ExtractionResult:
    """확장자에 따라 PDF 또는 촬영 이미지 추출기로 전달한다."""

    extension = Path(filename).suffix.lower()
    if extension == ".pdf":
        return extract_pdf_text(filename, data)
    if extension in IMAGE_EXTENSIONS:
        return extract_image_text(filename, data)
    raise DocumentValidationError("계약서는 PDF, JPG, JPEG, PNG 파일만 업로드할 수 있습니다.")
