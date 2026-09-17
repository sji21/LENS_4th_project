from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.AddConstraint(
        model_name="user",
        constraint=models.UniqueConstraint(
            Lower("email"), condition=~models.Q(email=""), name="unique_nonempty_user_email_ci",
        ),
    )]
