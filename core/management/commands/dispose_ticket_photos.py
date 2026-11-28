from django.core.management.base import BaseCommand, CommandError
from accounts.models import User
from tickets.photo_services import dispose_photos


class Command(BaseCommand):
    help = "按政策受控处置已关闭满12个月工单的照片，保留审计记录。"

    def add_arguments(self, parser):
        parser.add_argument("--ticket", type=int, required=True)
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--policy", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("需显式传入 --confirm，并核实政策依据与最低保留期。")
        try:
            count = dispose_photos(ticket_id=options["ticket"], actor=User.objects.get(pk=options["actor"]),
                                   reason=options["reason"], policy_reference=options["policy"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"已处置 {count} 张照片。"))
