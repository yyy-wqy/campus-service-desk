import io
import warnings
from pathlib import PurePosixPath

from django import forms
from django.core.exceptions import ValidationError
from PIL import Image

MAX_PHOTOS = 5
MAX_PHOTO_SIZE = 5 * 1024 * 1024


def validate_photos(uploads):
    if len(uploads) > MAX_PHOTOS:
        raise ValidationError("每张工单最多5张照片。")
    validated = []
    for upload in uploads:
        expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}.get(
            PurePosixPath(upload.name.replace("\\", "/")).suffix.lower()
        )
        if not expected or not upload.size or upload.size > MAX_PHOTO_SIZE:
            raise ValidationError("仅支持JPG/JPEG/PNG，单张不能为空且不得超过5MB。")
        data = upload.read(MAX_PHOTO_SIZE + 1)
        upload.seek(0)
        if len(data) > MAX_PHOTO_SIZE:
            raise ValidationError("单张照片不得超过5MB。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.format != expected or picture.width * picture.height > 16_000_000:
                        raise ValueError()
                    picture.verify()
                with Image.open(io.BytesIO(data)) as picture:
                    picture.load()
                    # Re-encode pixel data, discarding EXIF/location and appended content.
                    clean = Image.new("RGB" if expected == "JPEG" else "RGBA", picture.size)
                    clean.paste(picture.convert(clean.mode))
                    output = io.BytesIO()
                    clean.save(output, format=expected)
            data = output.getvalue()
            if len(data) > MAX_PHOTO_SIZE:
                raise ValueError()
        except Exception as exc:
            raise ValidationError("照片内容损坏、格式与扩展名不匹配或像素过大。") from exc
        validated.append((data, "image/jpeg" if expected == "JPEG" else "image/png",
                          ".jpg" if expected == "JPEG" else ".png", upload.name.replace("\\", "/").split("/")[-1][:200]))
    return validated


class MultiplePhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PhotoField(forms.FileField):
    widget = MultiplePhotoInput(attrs={"accept": ".jpg,.jpeg,.png"})

    def __init__(self, **kwargs):
        super().__init__(label="现场照片（选填）", required=False,
                         help_text="每单最多5张，单张不超过5MB，支持JPG/JPEG/PNG。", **kwargs)

    def clean(self, data, initial=None):
        uploads = data if isinstance(data, (list, tuple)) else [data] if data else []
        validate_photos(uploads)
        return uploads


class PhotoUploadForm(forms.Form):
    photos = PhotoField()

    def clean_photos(self):
        photos = self.cleaned_data["photos"]
        if not photos:
            raise ValidationError("请选择至少一张照片。")
        return photos
