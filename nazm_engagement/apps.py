from django.apps import AppConfig

class NazmEngagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nazm_engagement"

    def ready(self):
        import nazm_engagement.signals  # noqa: F401
