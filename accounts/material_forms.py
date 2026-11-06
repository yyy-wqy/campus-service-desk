import io
import warnings
from pathlib import PurePosixPath

from django import forms
from django.core.exceptions import ValidationError
from PIL import Image
from pypdf import PdfReader

from .models import MaintenanceMaterial


MAX_FILE_SIZE = 5 * 1024 * 1024


def validate_material(upload):
    if not upload.size or upload.size > MAX_FILE_SIZE:
        raise ValidationError("文件不能为空且单文件不得超过5MB。")
    extension = PurePosixPath(upload.name.replace("\\", "/")).suffix.lower()
    data = upload.read(MAX_FILE_SIZE + 1)
    upload.seek(0)
    if len(data) > MAX_FILE_SIZE:
        raise ValidationError("单文件不得超过5MB。")
    try:
        if extension == ".pdf":
            if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
                raise ValueError()
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or not len(reader.pages):
                raise ValueError()
            for page in reader.pages:
                page.get_contents()
            return data, "application/pdf", ".pdf"
        expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}.get(extension)
        if not expected:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != expected or picture.width * picture.height > 16_000_000:
                    raise ValueError()
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
                output = io.BytesIO()
                picture.save(output, format=expected)
        clean = output.getvalue()
        if len(clean) > MAX_FILE_SIZE:
            raise ValueError()
        return clean, "image/jpeg" if expected == "JPEG" else "image/png", ".jpg" if expected == "JPEG" else ".png"
    except Exception as error:
        raise ValidationError("仅接受内容有效且扩展名匹配的PDF/JPG/JPEG/PNG，不接受加密或损坏文件。") from error


class MaterialForm(forms.ModelForm):
    attachment = forms.FileField(label="附件（PDF/JPG/JPEG/PNG，最多5MB）", widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}))
    certificate_category = forms.CharField(label="证书类别", required=False, max_length=100, initial="其他", help_text="可填写具体类别，不确定时填写“其他”。")

    class Meta:
        model = MaintenanceMaterial
        fields = ("category", "name", "direction", "certificate_category", "certificate_number", "issuer", "issued_on", "expires_on", "permanent")
        widgets = {"issued_on": forms.DateInput(attrs={"type": "date"}), "expires_on": forms.DateInput(attrs={"type": "date"})}
        labels = {"category": "材料类型", "name": "资质名称（专业资质必填）", "direction": "专业方向（专业资质必填）"}
        help_texts = {"category": "工作关系证明只需上传附件；专业资质证明另需填写资质名称和专业方向。"}

    def clean_certificate_category(self):
        return self.cleaned_data.get("certificate_category") or "其他"

    def clean_attachment(self):
        upload = self.cleaned_data["attachment"]
        self.validated_file = validate_material(upload)
        return upload

    def clean(self):
        data = super().clean()
        if data.get("category") == MaintenanceMaterial.Category.QUALIFICATION:
            for field in ("name", "direction"):
                if not data.get(field):
                    self.add_error(field, "专业资质证明必须填写此项。")
        if data.get("permanent"):
            data["expires_on"] = None
        elif data.get("issued_on") and data.get("expires_on") and data["expires_on"] < data["issued_on"]:
            self.add_error("expires_on", "有效期不能早于发证日期。")
        return data


class MaintenanceReviewForm(forms.Form):
    result = forms.ChoiceField(label="审核结果", choices=[("approved", "通过"), ("needs_more", "要求补充"), ("rejected", "拒绝")])
    reason = forms.CharField(label="审核说明（补充或拒绝必填）", required=False, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get("result") in {"needs_more", "rejected"} and not data.get("reason"):
            self.add_error("reason", "要求补充或拒绝必须填写原因。")
        return data
