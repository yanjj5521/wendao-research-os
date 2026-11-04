"""``/team`` 入口守卫：先把身份说清楚，再决定给用户看什么。

本机挂载的同行会永远是"本机中心"这一份副本。用户如果连接的是别人的中心，
直接进这份副本只会看到空数据，还会误以为邀请码失效或版本包丢失。
所以这里在挂载层做一次身份分流：

- host：原样交给同行会应用（本机中心就是他的中心）；
- member：给出"你是成员，数据在 X"的说明页，主按钮打开真正的中心；
- unbound：给出两种身份的选择页，避免继续在空副本里摸索。

``?local=1`` 保留一个逃生门，供管理员排查本机副本时使用。
"""

from __future__ import annotations

from html import escape
from urllib.parse import parse_qs

from starlette.responses import HTMLResponse

from services import hub_identity

_PAGE_STYLE = """
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f2ec;color:#2c2a26;
font-family:"Microsoft YaHei",system-ui,sans-serif;padding:24px;box-sizing:border-box}
main{width:min(620px,100%);background:#fff;border:1px solid #e6e0d6;border-radius:16px;padding:32px 30px;
box-shadow:0 14px 40px rgba(60,50,30,.08)}
.eyebrow{font-size:12px;letter-spacing:.14em;color:#9a8f7d;text-transform:uppercase}
h1{font-size:22px;margin:6px 0 10px}
p{line-height:1.75;font-size:14px;color:#4a463f;margin:0 0 14px}
code{background:#f3efe8;border-radius:6px;padding:2px 6px;font-size:13px;word-break:break-all}
.row{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 8px}
a.btn,button.btn{display:inline-block;padding:11px 18px;border-radius:10px;font-size:14px;text-decoration:none;
border:1px solid transparent;cursor:pointer;font-family:inherit}
a.primary,button.primary{background:#3d8b6e;color:#fff}
a.soft,button.soft{background:#fff;color:#3d5a4c;border-color:#cdd8d0}
.note{font-size:12px;color:#8a8378;line-height:1.7;margin-top:16px;border-top:1px dashed #e6e0d6;padding-top:14px}
.tag{display:inline-block;font-size:12px;padding:2px 10px;border-radius:999px;background:#eef4f0;color:#3d8b6e}
form{margin:0}
"""


def _page(title: str, eyebrow: str, body: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{_PAGE_STYLE}</style></head>
<body><main>
<span class="eyebrow">{escape(eyebrow)}</span>
{body}
</main></body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


def _member_page(identity: dict) -> HTMLResponse:
    center = escape(str(identity["center_url"]))
    short = escape(hub_identity.display_address(str(identity["center_url"])))
    body = f"""<h1>你是成员，同行会在别人的电脑上 <span class="tag">成员</span></h1>
<p>你连接的联机中心是 <code>{center}</code>。团队数据、成员名单、福利和版本包都在那台机器上，
本机这份只是空副本。</p>
<div class="row">
  <a class="btn primary" href="{center}" target="_blank" rel="noopener">打开我连接的同行会（{short}）</a>
  <a class="btn soft" href="/online#identity-connect">不是我连的中心？去改连接</a>
</div>
<div class="note">想在本机重开一个自己的中心？先在联机页停止连接，再启动本机中心。
仅排查问题时，可<a href="/team/?local=1">打开本机副本</a>。</div>"""
    return _page("同行会 · 成员身份", "ResearchHub · member", body)


def _unbound_page() -> HTMLResponse:
    body = """<h1>先选择你的同行会身份 <span class="tag">未选择</span></h1>
<p>同行会有两种用法，流程完全不同：自己开中心当管理员，或连接别人的中心当成员。
选好之后「进入同行会」会一直打开对应那一份，不会再混在一起。</p>
<div class="row">
  <form method="post" action="/online/hub-host/start">
    <button class="btn primary" type="submit">① 这台电脑当中心（我先开）</button>
  </form>
  <a class="btn soft" href="/online#identity">② 连接别人的中心（我是成员）</a>
</div>
<div class="note">中心只有一个，成员可以有多个。管理者在自己电脑上启动中心后，
把「成员入口」地址和邀请码发给其他人；成员只需要地址和 Token。</div>"""
    return _page("同行会 · 选择身份", "ResearchHub · choose role", body)


class TeamEntryApp:
    """按身份分流 ``/team`` 的 ASGI 包装层。"""

    def __init__(self, hub_app) -> None:
        self.hub_app = hub_app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.hub_app(scope, receive, send)
            return
        if scope.get("type") == "websocket":
            await self.hub_app(scope, receive, send)
            return
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        if "1" in query.get("local", []):
            await self.hub_app(scope, receive, send)
            return
        identity = hub_identity.resolve()
        role = identity.get("role")
        if role == hub_identity.ROLE_HOST:
            await self.hub_app(scope, receive, send)
            return
        response = _member_page(identity) if role == hub_identity.ROLE_MEMBER else _unbound_page()
        await response(scope, receive, send)


def team_entry(hub_app) -> TeamEntryApp:
    return TeamEntryApp(hub_app)
