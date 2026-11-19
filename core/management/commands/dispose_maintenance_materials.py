from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.material_services import dispose_materials
from accounts.models import User


class Command(BaseCommand):
    help = "按校方政策受控处置正式停用满180天的维修材料，并保留审计记录。"

    def add_arguments(self, parser):
        parser.add_argument("--worker-id", type=int, required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--policy-reference", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("需显式传入 --confirm，且满足停用保留期与管理员权限。")
        try:
            worker = User.objects.get(pk=options["worker_id"], role="maintenance")
            actor = User.objects.get(pk=options["actor_id"])
            audit = dispose_materials(worker, actor, options["reason"], options["policy_reference"])
        except (User.DoesNotExist, PermissionDenied, ValidationError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"受控处置完成，审计记录 ID: {audit.pk}")
