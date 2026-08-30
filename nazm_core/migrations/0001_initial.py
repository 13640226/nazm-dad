from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators
import nazm_core.models
import nazm_core.storage

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="AudioTrack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=220)),
                ("description", models.TextField(blank=True)),
                ("audio_file", models.FileField(upload_to="audios/")),
                ("cover_image", models.ImageField(blank=True, null=True, upload_to="audio_covers/")),
                ("duration", models.DurationField(blank=True, null=True)),
                ("is_featured", models.BooleanField(default=False)),
                ("allow_download", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-is_featured", "-created_at"]},
        ),
        migrations.CreateModel(
            name="ContactMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("email", models.EmailField(max_length=254)),
                ("subject", models.CharField(blank=True, max_length=200)),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=220)),
                ("description", models.TextField(blank=True)),
                ("author", models.CharField(blank=True, max_length=160)),
                ("cover_image", models.ImageField(blank=True, null=True, upload_to="document_covers/")),
                ("pdf_file", models.FileField(blank=True, null=True, storage=nazm_core.storage.PrivateStorage(), upload_to=nazm_core.models.document_private_path, validators=[django.core.validators.FileExtensionValidator(["pdf"]), nazm_core.models.validate_pdf_content, nazm_core.models.validate_file_size])),
                ("access", models.CharField(choices=[("public", "Public"), ("login", "Authenticated users")], default="public", max_length=16)),
                ("is_published", models.BooleanField(default=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-published_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="DownloadLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("downloaded_at", models.DateTimeField(auto_now_add=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="downloads", to="nazm_core.document")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="nazm_downloads", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-downloaded_at"]},
        ),
    ]
