from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from mail.forms import MessageForm, AttachmentForm, AttachmentFormset
from mail.models import Message, ToGroup
from members.models import User


class FormBoundaryTests(TestCase):
    def setUp(self):
        # テスト用宛先グループ
        self.to_group = ToGroup.objects.create(year=2025, label="2025年度メンバー")

    # 1. MessageForm のバリデーションテスト
    def test_message_form_title_length(self):
        """タイトルの最大文字数を確認"""
        valid_title = "a" * 200
        invalid_title = "a" * 201

        # テスト用ユーザーを作成
        user = User.objects.create_user(email="test@example.com", year=2025)

        # 200文字のタイトル (有効な場合)
        form = MessageForm(
            data={
                "title": valid_title,
                "content": "テスト本文",
                "writer": user.id,  # 適切な`writer`IDを渡す
                "to_groups": [self.to_group.id],  # リスト形式で`to_groups`を渡す
            }
        )
        self.assertTrue(form.is_valid(), "200文字のタイトルは有効であるべき")

        # 201文字のタイトル (無効な場合)
        form = MessageForm(
            data={
                "title": invalid_title,
                "content": "テスト本文",
                "writer": user.id,
                "to_groups": [self.to_group.id],
            }
        )
        self.assertFalse(form.is_valid(), "201文字のタイトルは無効であるべき")

    def test_message_form_content_required(self):
        """本文が空の場合は無効"""
        form = MessageForm(
            data={
                "title": "テストタイトル",
                "content": "",
                "to_groups": [self.to_group.id],
            }
        )
        self.assertFalse(form.is_valid(), "本文が空の場合は無効であるべき")

    def test_message_form_to_groups_required(self):
        """宛先グループが選択されていない場合は無効"""
        form = MessageForm(
            data={"title": "テストタイトル", "content": "テスト本文", "to_groups": []}
        )
        self.assertFalse(
            form.is_valid(), "宛先グループが選択されていない場合は無効であるべき"
        )

    # 2. AttachmentForm のバリデーションテスト
    def test_attachment_form_file_size(self):
        """添付ファイルサイズのテスト"""
        small_file = SimpleUploadedFile("small.txt", b"a" * 2)  # 2バイト
        valid_file = SimpleUploadedFile("valid.txt", b"a" * 3)  # 3バイト
        large_file = SimpleUploadedFile(
            "large.txt", b"a" * (10 * 1024 * 1024 + 1)
        )  # 10MiB + 1バイト

        form = AttachmentForm(files={"file": small_file})
        self.assertFalse(form.is_valid(), "2バイトのファイルは無効であるべき")

        form = AttachmentForm(files={"file": valid_file})
        self.assertTrue(form.is_valid(), "3バイトのファイルは有効であるべき")

        form = AttachmentForm(files={"file": large_file})
        self.assertFalse(form.is_valid(), "10MBを超えるファイルは無効であるべき")

    # 3. AttachmentFormset のバリデーションテスト
    def test_attachment_formset_accepts_multiple_files(self):
        """合計29MiB（8 + 8 + 8 + 5）の複数添付を受け付ける。"""
        formset_data = {
            "attachments-TOTAL_FORMS": 4,
            "attachments-INITIAL_FORMS": 0,
            "attachments-MIN_NUM_FORMS": 0,
            "attachments-MAX_NUM_FORMS": 20,
        }
        files = {
            "attachments-0-file": SimpleUploadedFile("file1.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-1-file": SimpleUploadedFile("file2.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-2-file": SimpleUploadedFile("file3.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-3-file": SimpleUploadedFile("file4.txt", b"a" * (5 * 1024 * 1024)),
        }
        formset = AttachmentFormset(
            prefix="attachments", instance=Message(), data=formset_data, files=files
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(len(formset.cleaned_data), 4)

    def test_attachment_formset_rejects_total_size_over_limit(self):
        """合計31MiB（8 + 8 + 8 + 7）をDjango側の合計サイズ制限で拒否する。"""
        formset_data = {
            "attachments-TOTAL_FORMS": 4,
            "attachments-INITIAL_FORMS": 0,
            "attachments-MIN_NUM_FORMS": 0,
            "attachments-MAX_NUM_FORMS": 20,
        }
        files = {
            "attachments-0-file": SimpleUploadedFile("file1.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-1-file": SimpleUploadedFile("file2.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-2-file": SimpleUploadedFile("file3.txt", b"a" * (8 * 1024 * 1024)),
            "attachments-3-file": SimpleUploadedFile("file4.txt", b"a" * (7 * 1024 * 1024)),
        }
        formset = AttachmentFormset(
            prefix="attachments", instance=Message(), data=formset_data, files=files
        )
        self.assertFalse(formset.is_valid())
        self.assertTrue(all(not errors for errors in formset.errors), formset.errors)
        self.assertIn(
            "アップロードできるファイルの合計サイズは30MBまでです。",
            formset.non_form_errors(),
        )
