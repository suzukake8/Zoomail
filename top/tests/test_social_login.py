import re
from urllib.parse import parse_qs, urlparse

from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    SOCIAL_AUTH_AUTH0_KEY="test-auth0-client",
    SOCIAL_AUTH_GOOGLE_OAUTH2_KEY="test-google-client",
)
class SocialLoginStartTests(TestCase):
    def test_public_forms_start_login_with_csrf(self):
        for provider, host, client_id in (
            ("auth0", "patient-bar-7812.auth0.com", "test-auth0-client"),
            ("google-oauth2", "accounts.google.com", "test-google-client"),
        ):
            with self.subTest(provider=provider):
                client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
                page = client.get(reverse("top:top_page"), secure=True)
                self.assertEqual(page.status_code, 200)
                begin_url = reverse("social:begin", args=[provider])
                # 実際に表示されたPOSTフォームのCSRFトークンを送信する。
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

                # リダイレクトを追わないため、外部サービスへの通信は発生しない。
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
                params = parse_qs(target.query)
                self.assertEqual(params["client_id"], [client_id])
                self.assertEqual(
                    params["redirect_uri"],
                    ["https://localhost" + reverse("social:complete", args=[provider])],
                )
                self.assertTrue(params["state"][0])

    def test_login_start_rejects_post_without_csrf(self):
        for provider in ("auth0", "google-oauth2"):
            with self.subTest(provider=provider):
                client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
                client.get(reverse("top:top_page"), secure=True)

                response = client.post(
                    reverse("social:begin", args=[provider]),
                    secure=True,
                    HTTP_ORIGIN="https://localhost",
                )

                self.assertEqual(response.status_code, 403)

    def test_login_start_rejects_get(self):
        for provider in ("auth0", "google-oauth2"):
            with self.subTest(provider=provider):
                response = self.client.get(reverse("social:begin", args=[provider]))

                self.assertEqual(response.status_code, 405)
