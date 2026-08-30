from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from .storage import PrivateStorage

private_storage = PrivateStorage()

def private_filename(filename):
    return f"{uuid4().hex}{Path(filename).suffix.lower()}"

def document_private_path(instance, filename):
    return f"documents/{private_filename(filename)}"

def validate_file_size(file):
    max_size = 50 * 1024 * 1024
    if file.size > max_size:
        raise ValidationError("File cannot be larger than 50 MB.")

def validate_pdf_content(file):
    pos = file.tell()
    header = file.read(5)
    file.seek(pos)
    if header != b"%PDF-":
        raise ValidationError("Selected file is not a valid PDF.")

class Document(models.Model):
    ACCESS_PUBLIC = "public"
    ACCESS_LOGIN = "login"
    ACCESS_CHOICES = [
        (ACCESS_PUBLIC, "Public"),
        (ACCESS_LOGIN, "Authenticated users"),
    ]

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    author = models.CharField(max_length=160, blank=True)
    cover_image = models.ImageField(upload_to="document_covers/", blank=True, null=True)
    pdf_file = models.FileField(
        upload_to=document_private_path,
        storage=private_storage,
        validators=[
            FileExtensionValidator(["pdf"]),
            validate_pdf_content,
            validate_file_size,
        ],
        blank=True,
        null=True,
    )
    access = models.CharField(max_length=16, choices=ACCESS_CHOICES, default=ACCESS_PUBLIC)
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["access", "is_published"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

class AudioTrack(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    audio_file = models.FileField(upload_to="audios/")
    cover_image = models.ImageField(upload_to="audio_covers/", blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    is_featured = models.BooleanField(default=False)
    allow_download = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_featured", "-created_at"]

    def __str__(self):
        return self.title

class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

class DownloadLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nazm_downloads",
    )
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="downloads")
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-downloaded_at"]
