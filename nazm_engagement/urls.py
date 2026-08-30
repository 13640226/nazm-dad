from django.urls import path
from .views import summary

urlpatterns = [
    path("summary/", summary, name="engagement_summary"),
]
