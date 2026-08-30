from django.contrib import admin
from .models import AudioTrack, ContactMessage, Document, DownloadLog

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "access", "is_published", "published_at")
    list_filter = ("access", "is_published")
    search_fields = ("title", "slug", "author")
    prepopulated_fields = {"slug": ("title",)}

@admin.register(AudioTrack)
class AudioTrackAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_featured", "allow_download", "created_at")
    list_filter = ("is_featured", "allow_download")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}

admin.site.register(ContactMessage)
admin.site.register(DownloadLog)
