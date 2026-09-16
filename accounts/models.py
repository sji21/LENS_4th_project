from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """Extension point for the team's upcoming account features."""

    class Meta(AbstractUser.Meta):
        constraints = [models.UniqueConstraint(
            Lower("email"), condition=~models.Q(email=""), name="unique_nonempty_user_email_ci",
        )]
