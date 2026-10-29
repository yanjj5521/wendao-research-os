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
from features.review import register_review_routes
from features.workspaces import register_workspace_routes
from services.backups import register_backup_jobs
from hub.app import app as hub_app
from hub.db import init_hub_db
from web.gate import install_access_gate
from web.routes import analysis, foundation, home, library, research_tools, settings
from web.runtime import app, context, current_realm, entry_dict, flash, templates


def register_routes() -> None:
    # The team centre is a first-class page of the desktop product.  It still
    # keeps its own central database (multiple people must never write the
    # personal SQLite database), but no longer needs a second executable.
    init_hub_db()
    app.mount("/team", hub_app, name="team")
    for route_module in (
        home,
        library,
        settings,
        analysis,
        research_tools,
        foundation,
    ):
        app.include_router(route_module.app)

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
    register_project_routes(app, templates, context, flash)
    register_review_routes(app, templates, context, flash, current_realm)
    register_alchemy_routes(app, templates, context, flash)
    register_workspace_routes(app, templates, context, flash, entry_dict)
    register_backup_jobs(app)


init_db()
register_routes()
install_access_gate(app)
