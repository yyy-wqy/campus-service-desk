from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F

from accounts.models import User

from .models import (
    RepairConfirmation, RepairProgress, RepairResult, Ticket, TicketHistory,
)
from .permissions import can_confirm_ticket, can_process_ticket, current_business_user
from .services import _run_ticket_operation


class RepairConflict(ValidationError):
    """工单并发更新或当前状态不允许操作（HTTP 409）。"""


def require_repair_unfrozen(ticket):
    """供维修服务和取消申请模块共同使用；不改变主状态。"""
    if ticket.cancellation_pending:
        raise RepairConflict("取消申请待审核，暂不能推进维修流程。")


def _lock_ticket(ticket_id):
    if connection.vendor == "sqlite":
        # SQLite先取得写锁，再读取状态、版本和当前处理人。
        Ticket.objects.filter(pk=ticket_id).update(
            updated_at=F("updated_at")
        )
    return Ticket.objects.select_for_update().get(pk=ticket_id)


def _current_actor(actor):
    if not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("请先登录。")
    current = User.objects.select_for_update().filter(
        pk=getattr(actor, "pk", None)
    ).first()
    if current_business_user(current) is None:
        raise PermissionDenied("当前账号或维修资质不允许执行此操作。")
    return current


def _check_version(ticket, version):
    if type(version) is not int or version < 0:
        raise ValidationError("缺少有效操作版本，请刷新页面。")
    if ticket.repair_version != version:
        raise RepairConflict("工单已更新，请刷新后重试，勿重复提交旧页面。")


def _clean_content(content, limit):
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("请填写有效内容。")
    content = content.strip()
    if len(content) > limit:
        raise ValidationError(f"内容不能超过{limit}字。")
    return content


def _next_round(ticket):
    previous = ticket.repair_results.order_by("-round_number").first()
    if previous is None:
        return 1
    confirmation = RepairConfirmation.objects.filter(
        result=previous
    ).first()
    if confirmation is None or confirmation.decision != "unresolved":
        raise RepairConflict("上一轮结果尚未退回维修，不能建立新一轮记录。")
    return previous.round_number + 1


def _save_history(
    ticket, actor, old_status, description, metadata, event_type
):
    ticket.repair_version += 1
    ticket.save(
        update_fields=("status", "repair_version", "updated_at")
    )
    TicketHistory.objects.create(
        ticket=ticket,
        event_type=event_type,
        actor=actor,
        from_status=old_status,
        to_status=ticket.status,
        description=description,
        metadata=metadata,
    )


def _worker_action(
    *, ticket_id, actor, version, action, content=""
):
    with transaction.atomic():
        ticket = _lock_ticket(ticket_id)
        current = _current_actor(actor)
        if not can_process_ticket(current, ticket):
            raise PermissionDenied("仅当前分派的维修人员可以执行此操作。")
        require_repair_unfrozen(ticket)
        _check_version(ticket, version)

        required_status = (
            Ticket.Status.PENDING_WORK
            if action == "start"
            else Ticket.Status.IN_PROGRESS
        )
        if ticket.status != required_status:
            raise RepairConflict("工单当前状态不允许此操作，请刷新页面。")

        old_status = ticket.status
        round_number = _next_round(ticket)
        metadata = {
            "action": action,
            "round_number": round_number,
        }
        event_type = TicketHistory.EventType.STATUS_CHANGED

        if action == "start":
            ticket.status = Ticket.Status.IN_PROGRESS
            description = f"开始第{round_number}轮维修。"
        elif action == "progress":
            record = RepairProgress(
                ticket=ticket,
                worker=current,
                round_number=round_number,
                content=content,
            )
            record.full_clean()
            record.save()
            metadata["progress_id"] = record.pk
            event_type = TicketHistory.EventType.PROGRESS_ADDED
            description = f"第{round_number}轮维修进度：{content}"
        elif action == "result":
            record = RepairResult(
                ticket=ticket,
                worker=current,
                round_number=round_number,
                content=content,
            )
            record.full_clean()
            record.save()
            metadata["result_id"] = record.pk
            ticket.status = Ticket.Status.PENDING_CONFIRMATION
            description = f"第{round_number}轮维修结果：{content}"
        else:
            raise ValidationError("无效维修操作。")

        _save_history(
            ticket, current, old_status,
            description, metadata, event_type,
        )
        return ticket


def start_repair(*, ticket_id, actor, version):
    return _run_ticket_operation(
        lambda: _worker_action(
            ticket_id=ticket_id,
            actor=actor,
            version=version,
            action="start",
        )
    )


def add_repair_progress(*, ticket_id, actor, version, content):
    content = _clean_content(content, 2000)
    return _run_ticket_operation(
        lambda: _worker_action(
            ticket_id=ticket_id,
            actor=actor,
            version=version,
            action="progress",
            content=content,
        )
    )


def submit_repair_result(*, ticket_id, actor, version, content):
    content = _clean_content(content, 5000)
    return _run_ticket_operation(
        lambda: _worker_action(
            ticket_id=ticket_id,
            actor=actor,
            version=version,
            action="result",
            content=content,
        )
    )


def confirm_repair_result(
    *, ticket_id, actor, version, result_id, decision, reason=""
):
    if decision not in RepairConfirmation.Decision.values:
        raise ValidationError("请选择已解决或未解决。")
    if type(result_id) is not int or result_id < 1:
        raise ValidationError("缺少有效维修结果，请刷新页面。")
    if not isinstance(reason, str):
        raise ValidationError("未解决原因格式无效。")
    reason = reason.strip()
    if len(reason) > 2000:
        raise ValidationError("原因不能超过2000字。")
    if decision == "unresolved" and not reason:
        raise ValidationError("未解决时必须填写原因。")

    def operation():
        with transaction.atomic():
            ticket = _lock_ticket(ticket_id)
            current = _current_actor(actor)
            if not can_confirm_ticket(current, ticket):
                raise PermissionDenied("仅原报修人可以确认或退回维修结果。")

            # FR-CAN-005：待确认不允许申请取消；异常残留待审标记时拒绝推进。
            require_repair_unfrozen(ticket)
            _check_version(ticket, version)
            if ticket.status != Ticket.Status.PENDING_CONFIRMATION:
                raise RepairConflict("工单当前不是待确认状态，请刷新页面。")

            result = ticket.repair_results.order_by("-round_number").first()
            if result is None or result.pk != result_id:
                raise RepairConflict("维修结果已变化，请刷新后核对当前轮次。")
            if RepairConfirmation.objects.filter(result=result).exists():
                raise RepairConflict("本轮结果已确认，请勿重复提交。")

            record = RepairConfirmation(
                result=result,
                reporter=current,
                decision=decision,
                reason=reason,
            )
            record.full_clean()
            record.save()

            old_status = ticket.status
            if decision == "resolved":
                ticket.status = Ticket.Status.COMPLETED
                description = f"报修人确认第{result.round_number}轮维修已解决。"
            else:
                ticket.status = Ticket.Status.IN_PROGRESS
                description = (
                    f"报修人反馈第{result.round_number}轮未解决，退回维修：{reason}"
                )

            _save_history(
                ticket,
                current,
                old_status,
                description,
                {
                    "action": "repair_confirmation",
                    "round_number": result.round_number,
                    "result_id": result.pk,
                    "confirmation_id": record.pk,
                    "decision": decision,
                },
                TicketHistory.EventType.STATUS_CHANGED,
            )
            return ticket

    return _run_ticket_operation(operation)
