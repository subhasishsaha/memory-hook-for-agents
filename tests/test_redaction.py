"""Unit tests for secret and sensitive data redaction."""

import unittest

from memory_hook.redaction import is_sensitive_path, redact_payload, redact_text


class TestSecretRedaction(unittest.TestCase):
    def test_openai_and_anthropic_api_keys(self):
        text = "My OpenAI key is sk-abcdef1234567890abcdef1234567890 andAnthropic."
        redacted, was_red = redact_text(text)
        self.assertTrue(was_red)
        self.assertNotIn("sk-abcdef", redacted)
        self.assertIn("[REDACTED:API_KEY]", redacted)

    def test_github_tokens(self):
        text = "Access token: ghp_123456789012345678901234567890123456 and pat github_pat_11A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P7Q8R9S0T1U2V3W4X5Y6Z7"
        redacted, was_red = redact_text(text)
        self.assertTrue(was_red)
        self.assertIn("[REDACTED:GITHUB_TOKEN]", redacted)
        self.assertIn("[REDACTED:GITHUB_PAT]", redacted)
        self.assertNotIn("ghp_", redacted)

    def test_google_and_aws_keys(self):
        text = "Google AIzaSyD1234567890abcdef1234567890abcde AWS AKIAIOSFODNN7EXAMPLE"
        redacted, was_red = redact_text(text)
        self.assertTrue(was_red)
        self.assertIn("[REDACTED:GOOGLE_API_KEY]", redacted)
        self.assertIn("[REDACTED:AWS_KEY_ID]", redacted)

    def test_slack_and_bearer_tokens(self):
        text = "Slack xoxb-123456789012-abcdef123456 Header Bearer abcdef1234567890abcdef1234567890"
        redacted, was_red = redact_text(text)
        self.assertTrue(was_red)
        self.assertIn("[REDACTED:SLACK_TOKEN]", redacted)
        self.assertIn("Bearer [REDACTED:BEARER_TOKEN]", redacted)

    def test_private_key_blocks(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y1234567890abcdef...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        redacted, was_red = redact_text(pem)
        self.assertTrue(was_red)
        self.assertIn("[REDACTED:PRIVATE_KEY]", redacted)
        self.assertNotIn("MIIEowIBAAKCAQEA", redacted)

    def test_nested_payload_and_sensitive_files(self):
        payload = {
            "prompt": "Here is my secret password='SuperSecretPassword123' and api_key='sk-1234567890123456789012'",
            "tools": [
                {
                    "path": "/project/.env",
                    "content": "DB_PASSWORD=secret123\nAWS_SECRET_KEY=abcd12345\n",
                },
                {
                    "path": "src/main.py",
                    "content": "print('hello')",
                },
            ],
            "client_secret": "raw_sensitive_string",
        }

        clean_payload, was_redacted = redact_payload(payload)
        self.assertTrue(was_redacted)
        self.assertIn("[REDACTED:SECRET]", clean_payload["prompt"])
        self.assertEqual(clean_payload["tools"][0]["content"], "[REDACTED:SENSITIVE_FILE_CONTENT]")
        self.assertEqual(clean_payload["tools"][1]["content"], "print('hello')")
        self.assertEqual(clean_payload["client_secret"], "[REDACTED:SECRET]")

    def test_sensitive_path_detection(self):
        self.assertTrue(is_sensitive_path(".env"))
        self.assertTrue(is_sensitive_path(".env.local"))
        self.assertTrue(is_sensitive_path("config/production.env"))
        self.assertTrue(is_sensitive_path("~/.ssh/id_rsa"))
        self.assertTrue(is_sensitive_path("certs/server.key"))
        self.assertTrue(is_sensitive_path("certs/cert.pem"))
        self.assertTrue(is_sensitive_path("gcp_credentials.json"))
        self.assertFalse(is_sensitive_path("README.md"))
        self.assertFalse(is_sensitive_path("src/app.py"))


if __name__ == "__main__":
    unittest.main()
