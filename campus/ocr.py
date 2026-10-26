"""Optional local OCR. Model assets are prepared under models/ocr before offline use."""
import threading
from pathlib import Path

_engine = None
_lock = threading.Lock()
ROOT = Path(__file__).resolve().parents[1]


def get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr import RapidOCR
            _engine = RapidOCR(params={'Global.model_root_dir': str(ROOT / 'models' / 'ocr')})
        except ImportError as exc:
            raise ValueError('OCR 依赖未安装，请安装 requirements-ocr.txt。') from exc
    return _engine


def recognize_image(image):
    with _lock:
        try:
            result = get_engine()(image)
            return '\n'.join(result.txts or [])
        except Exception as exc:
            raise ValueError('本地 OCR 失败，请检查模型是否已准备，或改用文字版文件。') from exc


def extract_scanned_pages(content, page_numbers):
    try:
        import pymupdf
        import numpy as np
    except ImportError as exc:
        raise ValueError('PDF OCR 需要 PyMuPDF 与 RapidOCR，请安装 requirements-ocr.txt。') from exc
    results = {}
    with pymupdf.open(stream=content, filetype='pdf') as doc:
        for number in page_numbers:
            pix = doc[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7), alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            results[number] = recognize_image(image[:, :, ::-1].copy())
    return results
