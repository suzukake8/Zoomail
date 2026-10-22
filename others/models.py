from django.db import models
from private_storage.fields import PrivateFileField
from members.models import User
import uuid
import os
from io import BytesIO
from django.core.files.base import ContentFile
from PIL import Image
from pdf2image import convert_from_path


def others_custom_upload_to(instance, filename):
    ext = os.path.splitext(filename)[-1]
    return "others/{}{}".format(uuid.uuid4().hex, ext)


def others_custom_thumbnail_upload_to(instance, filename):
    return "others/{}_thumb.jpg".format(uuid.uuid4().hex)


class File(models.Model):
    class Meta:
        verbose_name = "その他ファイル"
        verbose_name_plural = "その他ファイル"

    filename = models.CharField(max_length=255, blank=True, null=False)
    file = PrivateFileField(upload_to=others_custom_upload_to)
    thumbnail = PrivateFileField(
        upload_to=others_custom_thumbnail_upload_to, blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_other_file",
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_other_file",
    )
    is_deleted = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.file and not self.thumbnail:
            self.generate_thumbnail()

    def generate_thumbnail(self):
        if self.file.name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            self.create_image_thumbnail()
        elif self.file.name.lower().endswith(".pdf"):
            self.create_pdf_thumbnail()

    def create_image_thumbnail(self):
        with Image.open(self.file.path) as img:
            img.thumbnail((256, 256))
            # Convert image to RGB if it has an alpha channel (e.g., RGBA)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            thumb_io = BytesIO()
            img.save(thumb_io, format="JPEG")
            thumb_content = ContentFile(thumb_io.getvalue())
            self.thumbnail.save(
                os.path.splitext(os.path.basename(self.file.name))[0] + "_thumb.jpg",
                thumb_content,
            )

    def create_pdf_thumbnail(self):
        images = convert_from_path(self.file.path, first_page=1, last_page=1)
        if images:
            # Resize the image to 256x256 pixels
            resized_image = images[0].resize((256, 256), Image.Resampling.LANCZOS)
            thumb_io = BytesIO()
            resized_image.save(thumb_io, format="JPEG")
            thumb_content = ContentFile(thumb_io.getvalue())
            self.thumbnail.save(
                os.path.splitext(os.path.basename(self.file.name))[0] + "_thumb.jpg",
                thumb_content,
            )
