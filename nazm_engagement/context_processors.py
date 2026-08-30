def user_points(request):
    if not request.user.is_authenticated:
        return {"nazm_user_points": 0}
    profile = getattr(request.user, "nazm_engagement_profile", None)
    return {"nazm_user_points": profile.total_points if profile else 0}
