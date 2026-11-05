import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import deploy_to_fabric as deploy


OPERATION = (
    "https://wabi-west-us-e-primary-redirect.analysis.windows.net"
    "/v1/operations/6c0617bf-18b2-49ae-a2ce-3baa9396a127"
)


class FabricClientTests(unittest.TestCase):
    def setUp(self):
        self.client = deploy.FabricClient.__new__(deploy.FabricClient)
        self.client.token = "test-token"
        self.client.expires = time.time() + 3600

    def response(self, status, body, headers=None):
        response = MagicMock()
        response.status = status
        response.headers = headers or {}
        response.read.return_value = deploy.json.dumps(body).encode("utf-8")
        response.__enter__.return_value = response
        return response

    def test_allowed_api_and_regional_operation_urls(self):
        for path in (
            "/workspaces",
            deploy.API + "/workspaces",
            OPERATION,
            OPERATION + "/result",
            OPERATION.replace(
                "6c0617bf-18b2-49ae-a2ce-3baa9396a127", "eb8c654e-6758-4aac-a578-ef93a4b5451a",
            ),
        ):
            with self.subTest(path=path):
                with patch.object(deploy, "urlopen", return_value=self.response(200, {})) as send:
                    self.assertEqual(self.client.request("GET", path), (200, {}, {}))
                    expected = path if path.startswith("https://") else deploy.API + path
                    self.assertEqual(send.call_args.args[0].full_url, expected)

    def test_rejects_untrusted_urls_and_regional_writes(self):
        for method, url in (
            ("GET", OPERATION.replace(".analysis.windows.net", ".analysis.windows.net.example.com")),
            ("GET", OPERATION.replace("wabi-west-us-e-primary-redirect", "unrelated")),
            ("GET", OPERATION.replace("/v1/operations/", "/v1/workspaces/")),
            ("GET", OPERATION.replace("6c0617bf-18b2-49ae-a2ce-3baa9396a127", "invalid")),
            ("GET", OPERATION + "/result/extra"),
            ("GET", OPERATION + "?redirect=example"),
            ("GET", OPERATION + "#fragment"),
            ("GET", OPERATION.replace("https://", "https://user@")),
            ("GET", OPERATION.replace(".net/", ".net:8443/")),
            ("GET", OPERATION.replace("https://", "http://")),
            ("GET", OPERATION.replace("https://", "//")),
            ("POST", OPERATION),
            ("DELETE", OPERATION),
            ("GET", "https://example.com/v1/operations/6c0617bf-18b2-49ae-a2ce-3baa9396a127"),
        ):
            with self.subTest(method=method, url=url):
                with patch.object(deploy, "urlopen") as send:
                    with self.assertRaisesRegex(ValueError, "Refusing untrusted API URL"):
                        self.client.request(method, url)
                    send.assert_not_called()

    def test_polls_regional_operation_and_reads_result(self):
        responses = [
            self.response(202, {}, {"Location": OPERATION, "Retry-After": "1"}),
            self.response(200, {"status": "Succeeded"}),
            self.response(200, {"id": "created-item"}),
        ]
        with patch.object(deploy, "urlopen", side_effect=responses) as send:
            with patch.object(deploy.time, "sleep"):
                result = self.client.call(
                    "POST", "/workspaces/target/items", {"displayName": "pipeline"}, result=True,
                )
        self.assertEqual(result, {"id": "created-item"})
        self.assertEqual(
            [call.args[0].full_url for call in send.call_args_list],
            [deploy.API + "/workspaces/target/items", OPERATION, OPERATION + "/result"],
        )
        self.assertEqual(
            [call.args[0].get_method() for call in send.call_args_list],
            ["POST", "GET", "GET"],
        )

    def test_standalone_package_loads(self):
        bundle = deploy.load_package(Path(__file__).with_name("package") / "manifest.json")
        self.assertEqual(len(bundle["exports"]), 3)


if __name__ == "__main__":
    unittest.main()
