from typing import List
import base64
import uuid
import requests

from django.db.models.functions import Coalesce, NullIf
from django.conf import settings
from django.db.models import Q, F, Value
from mail.models import Message, MailLog
from members.models import User

SEND_MAIL = settings.SEND_MAIL
SEND_MAIL_API_ENDPOINT = settings.SEND_MAIL_API_ENDPOINT
SEND_MAIL_API_ID = settings.SEND_MAIL_API_ID
SEND_MAIL_API_KEY = settings.SEND_MAIL_API_KEY


class MailContent:
    def __init__(
        self,
        subject: str = None,
        text: str = None,
        to_email: str = None,
        from_email: str = None,
        from_name: str = None,
        user=None,
        mail_id: str = None,
    ) -> None:
        self.subject = subject
        self.text = text
        self.to_email = to_email
        self.from_email = from_email
        self.from_name = from_name
        self.user = user
        self.mail_id = mail_id if mail_id else str(uuid.uuid4())

    def set_subject(self, subject: str):
        self.subject = subject

    def set_text(self, text: str):
        self.text = text

    def set_to(self, email: str):
        self.to_email = email

    def set_from(self, email: str, name: str):
        self.from_email = email
        self.from_name = name

    def set_user(self, user):
        self.user = user

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "text": self.text,
            "to": self.to_email,
            "from": {"email": self.from_email, "name": self.from_name},
            "id": self.mail_id,
        }


class MailAttachment:
    def __init__(self) -> None:
        self.filename = None
        self.base64encoded = None

    def load_file(self, filepath, filename=None):
        if filename is not None:
            self.filename = filename
        else:
            self.filename = filepath.split("/")[-1]
        with open(filepath, "rb") as f:
            data = f.read()
            f.close()
        self.base64encoded = base64.b64encode(data).decode("utf-8")

    def as_dict(self):
        return {"filename": self.filename, "content": self.base64encoded}


class SympleMailSender:

    def __init__(self) -> None:
        self.content_list: List[MailContent] = []
        self.attachment_list: List[MailAttachment] = []

    def set_content_list(self, content_list: List[MailContent]):
        self.content_list = content_list

    def set_attachment_list(self, attachment_list: List[MailAttachment]):
        self.attachment_list = attachment_list

    def _save_mail_logs(self):
        log_list = [
            MailLog(
                mail_id=content.mail_id,
                email=content.to_email,
                user=content.user,
                send_server=MailLog.SendServerChoices.SES,
                status=MailLog.StatusChoices.PENDING,
            )
            for content in self.content_list
        ]
        MailLog.objects.bulk_create(log_list)

    def _request(self):
        data = {
            "message": [content.as_dict() for content in self.content_list],
            "attachments": [
                attachment.as_dict() for attachment in self.attachment_list
            ],
        }
        headers = {
            "x-api-key": SEND_MAIL_API_KEY,
            "x-api-id": SEND_MAIL_API_ID,
            "Content-Type": "application/json",
        }
        response = requests.post(url=SEND_MAIL_API_ENDPOINT, json=data, headers=headers)
        response.raise_for_status()

    def send(self):
        if SEND_MAIL:
            self._save_mail_logs()
            self._request()
            return len(self.content_list)
        else:
            return 0


class MailisSender(SympleMailSender):

    def __init__(self, message: Message):
        super().__init__()
        self.message = message
        self.load_conent_list()
        self.load_attachment_list()

    def load_conent_list(self):
        if self.message.to_groups.filter(year=0).exists():
            send_to_users = User.objects.filter(
                send_mail=True,
            ).annotate(
                send_email=Coalesce(NullIf("receive_email", Value("")), F("email"))
            )
            content_list = [
                MailContent(
                    subject=self.message.title,
                    text=self.message.content
                    + "\n\n--------------------------------\nこのメーリスのURLはこちら\nhttps://zoomail.ku-unplugged.net/mail/"
                    + str(self.message.pk),
                    to_email=send_to_user.send_email,
                    from_email="zenkai@zoomail.ku-unplugged.net",
                    from_name=self.message.writer.get_short_name(),
                    user=send_to_user,
                )
                for send_to_user in send_to_users
            ]
            super().set_content_list(content_list)
        else:
            content_list = []
            for to_group in self.message.to_groups.all():
                send_to_users = User.objects.filter(
                    year=to_group.year, send_mail=True
                ).annotate(
                    send_email=Coalesce(NullIf("receive_email", Value("")), F("email"))
                )
                content_list += [
                    MailContent(
                        subject=self.message.title,
                        text=self.message.content
                        + "\n\n--------------------------------\nこのメーリスのURLはこちら\nhttps://zoomail.ku-unplugged.net/mail/"
                        + str(self.message.pk),
                        to_email=send_to_user.send_email,
                        from_email=f"{to_group.year - 1994}kaisei@zoomail.ku-unplugged.net",
                        from_name=self.message.writer.get_short_name(),
                        user=send_to_user,
                    )
                    for send_to_user in send_to_users
                ]
            super().set_content_list(content_list)

    def load_attachment_list(self):
        attachment_list = []
        for attachment in self.message.attachments.all():
            file = MailAttachment()
            file.load_file(attachment.file.path, filename=attachment.org_filename)
            attachment_list.append(file)
        super().set_attachment_list(attachment_list)

    def _save_mail_logs(self):
        log_list = [
            MailLog(
                message=self.message,
                mail_id=content.mail_id,
                email=content.to_email,
                user=content.user,
                send_server=MailLog.SendServerChoices.SES,
                status=MailLog.StatusChoices.PENDING,
            )
            for content in self.content_list
        ]
        MailLog.objects.bulk_create(log_list)


class MailisReSender(MailisSender):
    def load_conent_list(self):
        # MailLogでsuccessしていないものだけを再送信
        error_log_list = MailLog.objects.filter(message=self.message).filter(
            Q(status=MailLog.StatusChoices.FAILED)
            | Q(status=MailLog.StatusChoices.PENDING)
        )
        content_list = [
            MailContent(
                subject=self.message.title,
                text=self.message.content
                + "\n\n--------------------------------\nこのメーリスのURLはこちら\nhttps://zoomail.ku-unplugged.net/mail/"
                + str(self.message.pk),
                to_email=mail_log.email,
                from_email="zenkai@zoomail.ku-unplugged.net",
                from_name=self.message.writer.get_short_name(),
                user=mail_log.user,
                mail_id=mail_log.mail_id,
            )
            for mail_log in error_log_list
        ]
        super().set_content_list(content_list)

    def _save_mail_logs(self):
        return
