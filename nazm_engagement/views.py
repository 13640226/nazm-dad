from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

@login_required
def summary(request):
    profile = request.user.nazm_engagement_profile
    return JsonResponse({
        "points": profile.total_points,
        "level": profile.current_level.slug if profile.current_level else None,
        "public": profile.is_public,
    })
