from django.urls import path
from . import views

urlpatterns = [
    path("documents/", views.document_list_api, name="document_list_api"),
    path("audio/", views.audio_list_api, name="audio_list_api"),
    path("documents/<int:document_id>/download/", views.download_document, name="download_document"),
]
