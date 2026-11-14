from django.db import migrations

# 默认公共区域选项：走廊、楼梯间、卫生间、大厅、其他
DEFAULT_OPTIONS = (
    ("走廊", 1, False),
    ("楼梯间", 2, False),
    ("卫生间", 3, False),
    ("大厅", 4, False),
    ("其他", 5, True),
)


def seed_options(apps, schema_editor):
    PublicAreaOption = apps.get_model("locations", "PublicAreaOption")
    for name, sort_order, is_other in DEFAULT_OPTIONS:
        PublicAreaOption.objects.get_or_create(
            name=name,
            defaults={"sort_order": sort_order, "is_other": is_other, "is_active": True},
        )


def remove_options(apps, schema_editor):
    PublicAreaOption = apps.get_model("locations", "PublicAreaOption")
    PublicAreaOption.objects.filter(
        name__in=[name for name, _, _ in DEFAULT_OPTIONS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0002_publicareaoption_studenthousing_housingchangelog"),
    ]

    operations = [
        migrations.RunPython(seed_options, remove_options),
    ]
