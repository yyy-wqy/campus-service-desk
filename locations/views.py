from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.models import User

from .forms import (
    AdminHousingForm,
    BuildingForm,
    FaultCategoryForm,
    HousingForm,
    PublicAreaOptionForm,
)
from .housing import (
    change_own_building,
    get_housing,
    next_allowed_change,
    set_building_by_admin,
)
from .models import (
    Building,
    BuildingTypeChangeConflict,
    FaultCategory,
    HousingChangeLog,
    PublicAreaOption,
    StudentHousing,
)


def _require_admin(request):
    if request.user.role != User.Role.ADMIN:
        raise PermissionDenied


@login_required
@require_http_methods(["GET", "POST"])
def housing(request):
    """学生填写或更换本人住宿楼栋。"""

    if request.user.role != User.Role.STUDENT:
        raise PermissionDenied

    try:
        current = get_housing(request.user)
        history = list(
            request.user.housing_changes.select_related(
                "actor", "from_building", "to_building"
            ).order_by("-created_at", "-pk")
        )
    except ValidationError as exc:
        # 并发写锁读取失败：给可读提示，不返回 500。
        messages.error(request, exc.messages[0])
        current, history = None, []

    form = HousingForm(
        request.POST if request.method == "POST" else None,
        initial={"building": current.building_id} if current else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            change_own_building(request.user, form.cleaned_data["building"])
        except ValidationError as exc:
            # 换楼过于频繁、并发写锁竞争或楼栋状态变化，都落到表单提示。
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, "住宿信息已更新。")
            return redirect("locations:housing")

    return render(
        request,
        "locations/housing.html",
        {
            "form": form,
            "housing": current,
            "next_allowed_at": next_allowed_change(current),
            "history": history,
        },
    )


@login_required
@require_http_methods(["GET"])
def manage(request):
    """管理员维护楼栋、故障类别和公共区域。"""

    _require_admin(request)
    return render(
        request,
        "locations/manage.html",
        {
            "buildings": Building.objects.order_by("building_type", "name"),
            "categories": FaultCategory.objects.order_by("name"),
            "public_areas": PublicAreaOption.objects.order_by("sort_order", "name"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def manage_housing(request):
    """管理员代学生修改住宿楼栋，并留下操作人、时间和前后值。"""

    _require_admin(request)
    form = AdminHousingForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            set_building_by_admin(
                form.cleaned_data["student"],
                form.cleaned_data["building"],
                request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        else:
            messages.success(request, "住宿楼栋已更新，并已记录操作人、时间和前后楼栋。")
            return redirect("locations:manage_housing")

    return render(
        request,
        "locations/manage_housing.html",
        {
            "form": form,
            "assignments": StudentHousing.objects.select_related(
                "student", "building"
            ).order_by("student__username"),
            "logs": HousingChangeLog.objects.select_related(
                "student", "actor", "from_building", "to_building"
            ).order_by("-created_at", "-pk")[:20],
        },
    )


def _save_form(request, form, template, instance, success_message, label):
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except BuildingTypeChangeConflict as exc:
            # 并发兜底：校验通过后学生刚好建立了住宿档案。
            form.add_error("building_type", exc.messages[0])
        else:
            messages.success(request, success_message)
            return redirect("locations:manage")
    return render(
        request,
        template,
        {"form": form, "instance": instance, "page_title": label},
    )


@login_required
@require_http_methods(["GET", "POST"])
def building_form(request, pk=None):
    _require_admin(request)
    instance = get_object_or_404(Building, pk=pk) if pk else None
    form = BuildingForm(request.POST if request.method == "POST" else None, instance=instance)
    return _save_form(
        request,
        form,
        "locations/form_page.html",
        instance,
        "楼栋资料已保存。",
        "编辑楼栋" if instance else "新增楼栋",
    )


@login_required
@require_http_methods(["GET", "POST"])
def category_form(request, pk=None):
    _require_admin(request)
    instance = get_object_or_404(FaultCategory, pk=pk) if pk else None
    form = FaultCategoryForm(request.POST if request.method == "POST" else None, instance=instance)
    return _save_form(
        request,
        form,
        "locations/form_page.html",
        instance,
        "故障类别已保存。",
        "编辑故障类别" if instance else "新增故障类别",
    )


@login_required
@require_http_methods(["GET", "POST"])
def public_area_form(request, pk=None):
    _require_admin(request)
    instance = get_object_or_404(PublicAreaOption, pk=pk) if pk else None
    form = PublicAreaOptionForm(
        request.POST if request.method == "POST" else None, instance=instance
    )
    return _save_form(
        request,
        form,
        "locations/form_page.html",
        instance,
        "公共区域已保存。",
        "编辑公共区域" if instance else "新增公共区域",
    )


TOGGLE_MODELS = {
    "building": Building,
    "category": FaultCategory,
    "public-area": PublicAreaOption,
}


@login_required
@require_http_methods(["POST"])
def manage_toggle(request, kind, pk):
    """停用或启用基础资料；只切换状态，从不删除。"""

    _require_admin(request)
    model = TOGGLE_MODELS.get(kind)
    if model is None:
        raise Http404
    item = get_object_or_404(model, pk=pk)
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    messages.success(request, f"已{'启用' if item.is_active else '停用'}：{item.name}")
    return redirect("locations:manage")
