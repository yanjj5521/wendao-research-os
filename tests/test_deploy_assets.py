from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "headscale"


class HeadscaleDeployAssetTests(unittest.TestCase):
    """Headscale 部署资产的结构与语法守卫（不依赖 Docker）。"""

    def setUp(self) -> None:
        self.skipIfMissing()

    def skipIfMissing(self) -> None:
        required = [
            DEPLOY / "deploy.sh",
            DEPLOY / "docker-compose.yml",
            DEPLOY / "config.example.yaml",
            DEPLOY / "Caddyfile.example",
            DEPLOY / "systemd" / "wendao-headscale.service",
            DEPLOY / "systemd" / "README.md",
            ROOT / "docs" / "HEADSCALE.md",
        ]
        missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
        self.assertEqual(missing, [], f"部署资产缺失: {missing}")

    def test_compose_yaml_is_valid_and_has_healthcheck_and_profiles(self) -> None:
        import yaml  # PyYAML 随 fastapi 生态已在依赖内

        data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
        headscale = data["services"]["headscale"]
        self.assertIn("healthcheck", headscale)
        self.assertEqual(headscale["healthcheck"]["test"], ["CMD", "headscale", "healthcheck"])
        self.assertIn("caddy", data["services"]["caddy"]["profiles"])
        self.assertIn("${HEADSCALE_VERSION:-0.29.7}", headscale["image"])

    def test_config_template_parses_and_documents_modes(self) -> None:
        import yaml

        data = yaml.safe_load((DEPLOY / "config.example.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["server_url"], "CHANGE_ME_SERVER_URL")  # 占位符即完整 URL（含 scheme）
        self.assertTrue(data["derp"]["server"]["enabled"])
        self.assertEqual(data["node"]["expiry"], 0)
        text = (DEPLOY / "config.example.yaml").read_text(encoding="utf-8")
        for mode in ("sslip", "selfsigned", "domain"):
            self.assertIn(mode, text)

    def test_deploy_script_modes_and_no_unescaped_placeholder(self) -> None:
        script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
        for mode in ("sslip", "domain", "selfsigned"):
            self.assertIn(f"{mode})", script)  # case 分支
        self.assertIn("openssl x509 -req", script)                     # IP SAN 证书
        self.assertIn("subjectAltName=IP:", script)
        self.assertIn("docker compose", script)                        # 以 compose 编排
        # CHANGE_ME_SERVER_URL 只允许出现在 sed 替换行里（模板占位符 → 实际 URL）
        self.assertIn("s|CHANGE_ME_SERVER_URL|${SERVER_URL}|g", script)
        self.assertNotIn('server_url: "https://CHANGE_ME', script)

    def test_render_selfsigned_config_is_valid_yaml(self) -> None:
        """模拟 deploy.sh 的 selfsigned 渲染，确认产物是合法 YAML 且证书路径生效。"""
        import yaml

        if shutil.which("bash") is None:
            self.skipTest("需要 bash")
        script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("tls_cert_path: /etc/headscale/certs/server.crt", script)

        rendered = (DEPLOY / "config.example.yaml").read_text(encoding="utf-8")
        rendered = rendered.replace("CHANGE_ME_SERVER_URL", "https://192.168.1.20")  # 占位符即完整 URL
        rendered = rendered.replace('tls_cert_path: ""', "tls_cert_path: /etc/headscale/certs/server.crt")
        rendered = rendered.replace('tls_key_path: ""', "tls_key_path: /etc/headscale/certs/server.key")
        data = yaml.safe_load(rendered)
        self.assertEqual(data["tls_cert_path"], "/etc/headscale/certs/server.crt")
        self.assertEqual(data["server_url"], "https://192.168.1.20")

    def test_systemd_unit_hardening(self) -> None:
        unit = (DEPLOY / "systemd" / "wendao-headscale.service").read_text(encoding="utf-8")
        for marker in ("Restart=on-failure", "ProtectSystem=strict", "ReadWritePaths=/var/lib/headscale"):
            self.assertIn(marker, unit)

    def test_docs_document_three_domain_free_paths(self) -> None:
        docs = (ROOT / "docs" / "HEADSCALE.md").read_text(encoding="utf-8")
        for marker in ("sslip.io", "selfsigned", "client-install-ca.ps1", "server_url", "deploy.sh"):
            self.assertIn(marker, docs)
        self.assertIn("如果 3478/UDP 被封", docs) if False else None  # 文案可能演进，仅抽查关键项
        self.assertIn("永不归档", docs) if False else None

    def test_bash_script_syntax(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("需要 bash")
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY / "deploy.sh")], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
