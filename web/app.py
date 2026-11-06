from __future__ import annotations

from core.database import init_db
from features.assistant import register_assistant_routes
from features.career import register_career_routes
from features.daily import register_daily_routes
from features.discover import register_discover_routes
from features.folders import register_folder_routes
from features.foundation_ui import register_foundation_ui_routes
from features.game import register_game_routes, register_alchemy_routes, register_idle_routes
from features.sync import register_online_routes
from features.literature_report import register_literature_report_routes
from features.projects import register_project_routes
from features.practical_workbench import register_practical_workbench_routes
from features.professions import register_profession_routes
from features.reading import register_reading_routes
from features.review import register_review_routes
from features.workspaces import register_workspace_routes
from services.backups import register_backup_jobs
from hub.app import app as hub_app
from hub.db import init_hub_db
from hub.session_isolation import preserve_outer_session
from web.gate import install_access_gate
from web.team_entry import team_entry
from web.routes import analysis, foundation, home, integral, library, research_tools, roam, settings
from web.runtime import app, context, current_realm, entry_dict, flash, templates


def register_routes() -> None:
    # The team centre is a first-class page of the desktop product.  It still
    # keeps its own central database (multiple people must never write the
    # personal SQLite database), but no longer needs a second executable.
    # ``/team`` 先过身份分流：连接他人中心的成员不应该掉进本机这份空副本。
    init_hub_db()
    app.mount(
        "/team",
        team_entry(preserve_outer_session(hub_app)),
        name="team",
    )
    for route_module in (
        home,
        library,
        settings,
        analysis,
        research_tools,
        foundation,
        integral,
        roam,
    ):
        app.include_router(route_module.router)

    register_daily_routes(app, templates, context, flash)
    register_assistant_routes(app, templates, context, flash)
    register_career_routes(app, templates, context, flash)
    register_discover_routes(app, templates, context)
    register_folder_routes(app, templates, context)
    register_foundation_ui_routes(app, templates, context)
    register_game_routes(app, templates, context, flash, current_realm)
    register_online_routes(app, templates, context, flash)
    register_idle_routes(app, templates, context, flash)
    register_literature_report_routes(app, templates, context, flash)
    register_practical_workbench_routes(app, templates, context, flash)
    register_profession_routes(app, templates, context, flash)
    register_reading_routes(app, templates, context, flash)
    register_project_routes(app, templates, context, flash)
    register_review_routes(app, templates, context, flash, current_realm)
    register_alchemy_routes(app, templates, context, flash)
    register_workspace_routes(app, templates, context, flash, entry_dict)
    register_backup_jobs(app)

    @app.on_event("startup")
    def _resume_virtual_network() -> None:
        """上次开启过一键组网就自动恢复；放在后台线程，不阻塞窗口启动。"""
        import threading

        from services import headscale_vpn

        threading.Thread(
            target=headscale_vpn.resume_auto, daemon=True, name="headnet-autostart"
        ).start()
        from services import hub_realm

        threading.Thread(
            target=hub_realm.sync_if_due,
            kwargs={"automatic": True},
            daemon=True,
            name="realm-autostart",
        ).start()


init_db()
register_routes()
install_access_gate(app)
