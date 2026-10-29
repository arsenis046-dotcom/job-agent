"""Рендер резюме.

Формат одноколоночный, стандартные заголовки, текстовый слой в PDF —
чтобы ATS читал резюме как есть. Никаких скрытых ключевых слов.

Устройство: `resume.py` собирает документ из фактов мастер-профиля (текст
строки — вычисляемое свойство, выдумать его нельзя), `render.py` печатает
DOCX и PDF, `ats.py` извлекает текст обратно и проверяет, что всё читается
и порядок разделов не поехал.
"""

from .ats import AtsReport, check_pdf, extract_text
from .render import RenderError, find_font, render_docx, render_pdf
from .resume import HEADINGS, LABELS, Line, Resume, ResumeError, Section, build_resume

__all__ = [
    "AtsReport",
    "HEADINGS",
    "LABELS",
    "Line",
    "RenderError",
    "Resume",
    "ResumeError",
    "Section",
    "build_resume",
    "check_pdf",
    "extract_text",
    "find_font",
    "render_docx",
    "render_pdf",
]
