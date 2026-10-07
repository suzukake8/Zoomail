import base64
import tempfile
from unittest.mock import patch

from django.test import TestCase
from django.core.files.storage import FileSystemStorage
from django.urls import reverse
from django.conf import settings
from django.test.utils import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from members.models import User
from mail.models import ToGroup, Message, MailLog


class SendViewTests(TestCase):
    def setUp(self):
        # ユーザーを作成
        self.user = User.objects.create_user(
            email="testuser@example.com",
            year=2025,
        )
        self.user.fullname = "テストユーザー"
        self.user.furigana = "てすと"
        self.user.save()
        self.send_url = reverse("mail:send")

    def test_send_view_redirects_if_not_logged_in(self):
        """
        未ログインユーザーはメール送信ページにアクセスすると
        ログインページへリダイレクトされること
        """
        response = self.client.get(self.send_url)
        self.assertEqual(response.status_code, 302)
        expected_redirect = f"{settings.LOGIN_URL}?next={self.send_url}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

    def test_send_view_accessible_for_logged_in_user(self):
        """
        ログイン済みユーザーはメール送信ページへアクセスできること
        """
        self.client.force_login(self.user)
        response = self.client.get(self.send_url)
        self.assertEqual(response.status_code, 200)

    def test_send_redirects_to_first_register_if_user_incomplete(self):
        """
        fullnameとfuriganaが未設定の場合first_registerにリダイレクトされること
        """
        incomplete_user = User.objects.create_user(
            email="incomplete@example.com",
            year=2025,
        )
        # fullnameとfuriganaを設定しない
        self.client.force_login(incomplete_user)
        response = self.client.get(self.send_url)
        expected_url = reverse("members:first_register")
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_send_create_fill_form(self):
        """
        メール送信フォームに必要項目を入力し、Compose -> Confirm -> Complete の
        確認画面・複数添付の保存・送信APIへ渡す元の名前と内容を検証する。
        外部APIはモックし、SESによる受け付けや配送は検証しない。
        """
        # withを抜けると、一時ファイルの削除と設定・モックの復元が行われる。
        with (
            tempfile.TemporaryDirectory() as storage_dir,
            override_settings(PRIVATE_STORAGE_ROOT=storage_dir),
            # 確認画面用の添付も、テスト専用の一時フォルダに保存する。
            patch(
                "mail.views.send.SendWizardView.file_storage",
                FileSystemStorage(location=storage_dir + "/tmp"),
            ),
            # 送信処理を有効にし、実際のHTTP通信だけをモックに置き換える。
            patch("mail.send.SEND_MAIL", True),
            patch("mail.send.requests.post") as mock_post,
        ):
            # グループ作成
            group = ToGroup.objects.create(year=2025, label="2025年度メンバー")
            self.client.force_login(self.user)

            # Step 1: Compose 表示を取得して管理フォーム・フォームセットを取得
            resp1 = self.client.get(self.send_url)
            wizard = resp1.context["wizard"]
            management_form = wizard["management_form"]
            attachment_formset = resp1.context["attachment_formset"]

            # Compose ステップ用の POST データを準備
            post = {"send_wizard_view-current_step": "compose"}
            post.update(
                {
                    "writer": str(self.user.id),
                    "to_groups": [str(group.id)],
                    "title": "テスト件名",
                    "content": "テスト本文",
                }
            )

            # wizard 管理用 hidden フィールドを追加
            for field in management_form.hidden_fields():
                post[field.name] = field.value()

            # attachment formset の管理用 hidden フィールドを追加
            for field in attachment_formset.management_form.hidden_fields():
                post[field.name] = field.value()
            post["attachments-TOTAL_FORMS"] = str(attachment_formset.total_form_count())
            post["attachments-INITIAL_FORMS"] = str(attachment_formset.initial_form_count())

            # ファイルもdataに含め、実際のmultipartアップロードを行う。
            attachments = {"資料.txt": b"abc", "image.png": b"\x89PNG\r\n\x1a\n"}
            post["attachments-TOTAL_FORMS"] = str(len(attachments))
            for index, (filename, content) in enumerate(attachments.items()):
                post[f"{attachment_formset.prefix}-{index}-file"] = (
                    SimpleUploadedFile(filename, content)
                )

            # Step 1 -> Step 2 (confirm) へ移行
            resp2 = self.client.post(self.send_url, data=post)
            self.assertEqual(resp2.status_code, 200)
            self.assertTemplateUsed(resp2, "mail/send_confirm.html")
            for filename in attachments:
                self.assertContains(resp2, filename)
            self.assertEqual(Message.objects.count(), 0)
            mock_post.assert_not_called()

            # Step 2: Confirm 送信を実行 (Complete 実行)
            resp3 = self.client.post(
                self.send_url,
                data={"send_wizard_view-current_step": "confirm"},
            )
            self.assertRedirects(resp3, reverse("mail:inbox"))

            # 送信後に Message が作成されていることを検証
            self.assertEqual(Message.objects.count(), 1)
            last = Message.objects.filter(sender=self.user).order_by("-id").first()
            self.assertIsNotNone(last)
            self.assertEqual(last.title, "テスト件名")
            self.assertEqual(last.content, "テスト本文")
            self.assertEqual(last.sender, self.user)
            self.assertTrue(last.to_groups.filter(id=group.id).exists())
            self.assertEqual(last.attachments.count(), len(attachments))
            for attachment in last.attachments.all():
                with attachment.file.open("rb") as saved_file:
                    self.assertEqual(saved_file.read(), attachments[attachment.org_filename])

            mock_post.assert_called_once()
            mock_post.return_value.raise_for_status.assert_called_once()
            payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(len(payload["attachments"]), len(attachments))
            self.assertEqual(
                {
                    item["filename"]: base64.b64decode(item["content"])
                    for item in payload["attachments"]
                },
                attachments,
            )
            self.assertEqual(len(payload["message"]), 1)
            log = MailLog.objects.get(message=last)
            self.assertEqual(log.mail_id, payload["message"][0]["id"])
            self.assertEqual(log.status, MailLog.StatusChoices.PENDING)

    @override_settings(SEND_MAIL=False)
    def test_send_validation_error_when_title_missing(self):
        group = ToGroup.objects.create(year=2025, label="2025年度メンバー")
        self.client.force_login(self.user)

        response = self.client.get(self.send_url)
        management_form = response.context["wizard"]["management_form"]
        attachment_formset = response.context["attachment_formset"]

        post = {
            "send_wizard_view-current_step": "compose",
            "writer": str(self.user.id),
            "to_groups": [str(group.id)],
            "title": "",
            "content": "テスト本文",
            "attachments-TOTAL_FORMS": str(attachment_formset.total_form_count()),
            "attachments-INITIAL_FORMS": str(attachment_formset.initial_form_count()),
        }
        for field in management_form.hidden_fields():
            post[field.name] = field.value()
        for field in attachment_formset.management_form.hidden_fields():
            post[field.name] = field.value()

        response = self.client.post(self.send_url, data=post)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mail/send_create.html")
        self.assertTrue(response.context["message_form"].errors)
        self.assertIn("title", response.context["message_form"].errors)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(SEND_MAIL=False)
    def test_send_validation_error_when_content_missing(self):
        group = ToGroup.objects.create(year=2025, label="2025年度メンバー")
        self.client.force_login(self.user)

        response = self.client.get(self.send_url)
        management_form = response.context["wizard"]["management_form"]
        attachment_formset = response.context["attachment_formset"]

        post = {
            "send_wizard_view-current_step": "compose",
            "writer": str(self.user.id),
            "to_groups": [str(group.id)],
            "title": "件名",
            "content": "",
            "attachments-TOTAL_FORMS": str(attachment_formset.total_form_count()),
            "attachments-INITIAL_FORMS": str(attachment_formset.initial_form_count()),
        }
        for field in management_form.hidden_fields():
            post[field.name] = field.value()
        for field in attachment_formset.management_form.hidden_fields():
            post[field.name] = field.value()

        response = self.client.post(self.send_url, data=post)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mail/send_create.html")
        self.assertTrue(response.context["message_form"].errors)
        self.assertIn("content", response.context["message_form"].errors)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(SEND_MAIL=False)
    def test_send_confirm_direct_post_does_not_create_message(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.send_url,
            data={"send_wizard_view-current_step": "confirm"},
            follow=True,
        )

        self.assertIn(response.status_code, (200, 400))
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(SEND_MAIL=False)
    def test_send_confirm_page_displays_composed_values(self):
        group = ToGroup.objects.create(year=2025, label="2025年度メンバー")
        self.client.force_login(self.user)

        resp1 = self.client.get(self.send_url)
        management_form = resp1.context["wizard"]["management_form"]
        attachment_formset = resp1.context["attachment_formset"]

        post = {
            "send_wizard_view-current_step": "compose",
            "writer": str(self.user.id),
            "to_groups": [str(group.id)],
            "title": "確認件名",
            "content": "確認本文",
            "attachments-TOTAL_FORMS": str(attachment_formset.total_form_count()),
            "attachments-INITIAL_FORMS": str(attachment_formset.initial_form_count()),
        }
        for field in management_form.hidden_fields():
            post[field.name] = field.value()
        for field in attachment_formset.management_form.hidden_fields():
            post[field.name] = field.value()

        response = self.client.post(self.send_url, data=post, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mail/send_confirm.html")
        self.assertContains(response, "確認件名")
        self.assertContains(response, "確認本文")
        self.assertContains(response, "2025年度メンバー")

    @override_settings(SEND_MAIL=False)
    def test_send_validation_error_when_to_groups_missing(self):
        self.client.force_login(self.user)

        response = self.client.get(self.send_url)
        management_form = response.context["wizard"]["management_form"]
        attachment_formset = response.context["attachment_formset"]

        post = {
            "send_wizard_view-current_step": "compose",
            "writer": str(self.user.id),
            "to_groups": [],
            "title": "件名",
            "content": "本文",
            "attachments-TOTAL_FORMS": str(attachment_formset.total_form_count()),
            "attachments-INITIAL_FORMS": str(attachment_formset.initial_form_count()),
        }
        for field in management_form.hidden_fields():
            post[field.name] = field.value()
        for field in attachment_formset.management_form.hidden_fields():
            post[field.name] = field.value()

        response = self.client.post(self.send_url, data=post)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mail/send_create.html")
        self.assertIn("to_groups", response.context["message_form"].errors)
        self.assertEqual(Message.objects.count(), 0)
