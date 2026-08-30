from django.conf import settings
from django.db import models

class Level(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True)
    min_points = models.PositiveIntegerField(unique=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["min_points"]

    def __str__(self):
        return f"{self.name} ({self.min_points}+)"

class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="nazm_engagement_profile",
    )
    total_points = models.PositiveIntegerField(default=0)
    current_level = models.ForeignKey(
        Level, null=True, blank=True, on_delete=models.SET_NULL, related_name="users"
    )
    is_public = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class PointTransaction(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nazm_point_transactions",
    )
    action = models.CharField(max_length=50)
    points = models.IntegerField()
    reference_id = models.CharField(max_length=150, blank=True)
    idempotency_key = models.CharField(max_length=200, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_nazm_user_engagement_idempotency",
            )
        ]
