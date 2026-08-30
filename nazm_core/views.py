from pathlib import Path
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse, Http404
from django.views.decorators.http import require_GET

from .models import AudioTrack, Document, DownloadLog

def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

@require_GET
def health_view(request):
    return JsonResponse({"status": "ok", "service": "nazm-dad"})

@require_GET
def document_list_api(request):
    items = Document.objects.filter(is_published=True).values(
        "id", "slug", "title", "description", "author", "access", "published_at"
    )
    return JsonResponse({"results": list(items)})

@require_GET
def audio_list_api(request):
    items = AudioTrack.objects.all().values(
        "id", "slug", "title", "description", "duration",
        "is_featured", "allow_download", "created_at"
    )
    return JsonResponse({"results": list(items)})

@require_GET
def download_document(request, document_id):
    try:
        document = Document.objects.get(pk=document_id, is_published=True)
    except Document.DoesNotExist as exc:
        raise Http404 from exc

    if document.access == Document.ACCESS_LOGIN and not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication_required"}, status=401)

    if not document.pdf_file:
        raise Http404

    DownloadLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        document=document,
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
    )

    path = Path(document.pdf_file.path)
    if not path.exists():
        raise Http404

    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=path.name,
        content_type="application/pdf",
    )
