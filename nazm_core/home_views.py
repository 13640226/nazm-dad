from pathlib import Path

from django.conf import settings
from django.http import HttpResponse


BASE_DIR = Path(settings.BASE_DIR)


def serve_html_file(filename):
    """
    Serve an existing Nazm Dad HTML file without modifying its
    Persian text, design, links or branding.
    """

    file_path = BASE_DIR / filename

    if not file_path.exists():
        return HttpResponse(
            f"{filename} not found",
            status=404,
            content_type="text/plain; charset=utf-8",
        )

    try:
        html = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        html = file_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )

    return HttpResponse(
        html,
        content_type="text/html; charset=utf-8",
    )


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------

def home(request):
    return serve_html_file("index.html")


# ---------------------------------------------------------
# LIBRARY
# ---------------------------------------------------------

def library_page(request):
    return serve_html_file("library.html")


def reader_page(request):
    return serve_html_file("reader.html")


def audio_page(request):
    return serve_html_file("audio.html")


# ---------------------------------------------------------
# PRINCIPLES
# ---------------------------------------------------------

def principles_index_page(request):
    return serve_html_file("principles-index.html")


def principle_1_page(request):
    return serve_html_file("principle-1.html")


def principle_2_page(request):
    return serve_html_file("principle-2.html")


def principle_3_page(request):
    return serve_html_file("principle-3.html")


# ---------------------------------------------------------
# OFFLINE
# ---------------------------------------------------------

def offline_page(request):
    return serve_html_file("offline.html")