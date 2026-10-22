import re
from urllib.parse import parse_qs, urlparse

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from members.models import User


@override_settings(
    SOCIAL_AUTH_AUTH0_KEY="test-auth0-client",
    SOCIAL_AUTH_GOOGLE_OAUTH2_KEY="test-google-client",
)
class ProfileSocialLoginStartTests(TestCase):
    def test_profile_forms_preserve_return_path(self):
        user = User.objects.create_user(email="link@example.com", year=2024)
        user.fullname = "連携ユーザー"
        user.furigana = "れんけいゆーざー"
        user.save()
        profile_url = reverse("mypage:profile")

        for provider, host in (
            ("auth0", "patient-bar-7812.auth0.com"),
            ("google-oauth2", "accounts.google.com"),
        ):
            with self.subTest(provider=provider):
                client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
                client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
                page = client.get(profile_url, secure=True)
                self.assertEqual(page.status_code, 200)
                begin_url = reverse("social:begin", args=[provider])
                form = re.search(
                    rf'<form\b(?=[^>]*\baction="{re.escape(begin_url)}")'
                    r'(?=[^>]*\bmethod="post")[^>]*>(.*?)</form>',
                    page.content.decode(),
                    re.DOTALL,
                )
                self.assertIsNotNone(form)
                data = dict(
                    re.findall(
                        r'<input\b[^>]*\bname="([^"]+)"[^>]*\bvalue="([^"]*)"',
                        form.group(1),
                    )
                )
                self.assertIn("csrfmiddlewaretoken", data)
                self.assertEqual(data["next"], profile_url)

                response = client.post(
                    begin_url,
                    data=data,
                    secure=True,
                    HTTP_ORIGIN="https://localhost",
                )

                self.assertEqual(response.status_code, 302)
                target = urlparse(response["Location"])
                self.assertEqual(target.scheme, "https")
                self.assertEqual(target.hostname, host)
                self.assertEqual(client.session["next"], profile_url)
                params = parse_qs(target.query)
                self.assertEqual(
                    params["redirect_uri"],
                    ["https://localhost" + reverse("social:complete", args=[provider])],
                )
                self.assertTrue(params["state"][0])
