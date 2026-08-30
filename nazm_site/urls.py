from django.contrib import admin
from django.urls import include, path

from nazm_core import views as core_views

from nazm_core.home_views import (
    home,
    library_page,
    reader_page,
    audio_page,
    principles_index_page,
    principle_1_page,
    principle_2_page,
    principle_3_page,
    offline_page,
)


urlpatterns = [

    # =====================================================
    # NAZM DAD EXISTING WEBSITE
    # =====================================================

    # Home
    path("", home, name="home"),
    path("index.html", home, name="index"),

    # Library
    path(
        "library.html",
        library_page,
        name="library",
    ),

    path(
        "reader.html",
        reader_page,
        name="reader",
    ),

    path(
        "audio.html",
        audio_page,
        name="audio",
    ),

    # Principles
    path(
        "principles-index.html",
        principles_index_page,
        name="principles-index",
    ),

    path(
        "principle-1.html",
        principle_1_page,
        name="principle-1",
    ),

    path(
        "principle-2.html",
        principle_2_page,
        name="principle-2",
    ),

    path(
        "principle-3.html",
        principle_3_page,
        name="principle-3",
    ),

    # Offline
    path(
        "offline.html",
        offline_page,
        name="offline",
    ),

    # =====================================================
    # DJANGO / TECHNICAL FOUNDATION
    # =====================================================

    path(
        "admin/",
        admin.site.urls,
    ),

    path(
        "accounts/",
        include("allauth.urls"),
    ),

    path(
        "api/",
        include("nazm_core.urls"),
    ),

    path(
        "engagement/",
        include("nazm_engagement.urls"),
    ),

    path(
        "health/",
        core_views.health_view,
        name="health",
    ),
]