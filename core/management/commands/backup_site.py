from django.core.management.base import BaseCommand, CommandError

from core.backups import create_backup


class Command(BaseCommand):
    help = "一致性备份 SQLite、公开附件和私有材料，保留最近7个成功备份日。"

    def handle(self, *args, **options):
        try:
            target = create_backup()
        except Exception as error:
            raise CommandError(f"备份失败（{type(error).__name__}），请检查私有执行记录。") from error
        self.stdout.write(self.style.SUCCESS(str(target)))
