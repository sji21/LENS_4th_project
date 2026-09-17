from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cases", "0002_remove_empty_legacy_default_cases")]

    operations = [
        migrations.AddField(
            model_name="contractcase",
            name="guidance_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
