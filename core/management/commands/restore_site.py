from django.core.management.base import BaseCommand, CommandError

from core.backups import restore_backup


class Command(BaseCommand):
    help = "校验并恢复到一个不存在的独立目录，不覆盖当前工作库。"

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--destination", required=True)

    def handle(self, *args, **options):
        try:
            target = restore_backup(options["source"], options["destination"])
        except Exception as error:
            raise CommandError(f"恢复失败（{type(error).__name__}），目标必须独立且校验通过。") from error
        self.stdout.write(self.style.SUCCESS(str(target)))
