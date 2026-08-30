from django.conf import settings
from django.core.files.storage import FileSystemStorage

class PrivateStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs["location"] = settings.PRIVATE_ROOT
        kwargs["base_url"] = None
        super().__init__(*args, **kwargs)

    def url(self, name):
        raise NotImplementedError("Private files do not expose public URLs.")
