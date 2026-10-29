"""挂机静修契约测试：页面、配置保存、壁纸上传与库上限、CSRF/同源防护。"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from web.app import app
from services.idle_space import WALLPAPER_DIR


def _client() -> TestClient:
    return TestClient(app)


class IdlePageTests(unittest.TestCase):
    def test_idle_page_renders_with_v2_timer(self) -> None:
        with _client() as client:
            page = client.get("/idle")
            self.assertEqual(page.status_code, 200)
            for marker in ("挂机静修", "wendao-idle-v2", "data-idle-clock", "data-idle-note"):
                self.assertIn(marker, page.text)

    def test_config_save_roundtrip(self) -> None:
        with _client() as client:
            client.get("/idle")
            saved = client.post(
                "/idle/config",
                data={"layout": "horizontal", "focus": "只推进一个概念"},
                follow_redirects=True,
            )
            self.assertEqual(saved.status_code, 200)
            again = client.get("/idle")
            self.assertIn("只推进一个概念", again.text)
            self.assertIn("横向案台", again.text)

    def test_wallpaper_upload_validates_and_prunes(self) -> None:
        import io

        from PIL import Image

        with _client() as client:
            client.get("/idle")

            # 非图片：拒绝
            bad = client.post(
                "/idle/wallpaper",
                files={"file": ("fake.png", b"not-an-image", "image/png")},
                follow_redirects=False,
            )
            self.assertEqual(bad.status_code, 303)

            # 合法图片：连传 13 张，库上限 12 张
            for index in range(13):
                buffer = io.BytesIO()
                Image.new("RGB", (32, 20), (20 * index % 255, 120, 90)).save(buffer, format="PNG")
                response = client.post(
                    "/idle/wallpaper",
                    files={"file": (f"w{index}.png", buffer.getvalue(), "image/png")},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)

            wallpapers = list(WALLPAPER_DIR.glob("idle_wallpaper_*.webp"))
            self.assertLessEqual(len(wallpapers), 12, wallpapers)

    def test_cross_site_write_is_rejected(self) -> None:
        with _client() as client:
            client.get("/idle")
            evil = client.post(
                "/idle/config",
                data={"layout": "horizontal"},
                headers={"Origin": "https://evil.example"},
            )
            self.assertEqual(evil.status_code, 403)


if __name__ == "__main__":
    unittest.main()
