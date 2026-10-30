from __future__ import annotations

"""个人端建表定义（schema v2）。

与 3.x 的差异：
- 数据库文件改为 `wendao.db`，版本号写进 `PRAGMA user_version`；
- 移除三张遗留表（research_tracks / research_plan_items / track_growth），
  它们服务的是已下线的"科研主线"页面；
- 为真实查询路径补齐索引，避免数据量增长后出现全表扫描；
- 新增 `schema_migrations`，为后续版本升级留出可追溯的入口。
"""

SCHEMA_VERSION = 5
DATABASE_FILENAME = "wendao.db"

BASE_SCHEMA_SQL = r"""
-- 遗留表：仍被 daily_missions.track_id 与成就统计引用，
-- 仅保留结构供旧数据存在，不再写入任何种子数据。
CREATE TABLE research_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                icon TEXT NOT NULL DEFAULT '◇',
                objective TEXT NOT NULL DEFAULT '',
                current_stage TEXT NOT NULL DEFAULT '',
                next_focus TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
CREATE TABLE research_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                deliverable TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                priority TEXT NOT NULL DEFAULT 'normal',
                due_date TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES research_tracks(id) ON DELETE CASCADE
            );
CREATE TABLE track_growth (
                track_id INTEGER PRIMARY KEY,
                bonus_points INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES research_tracks(id) ON DELETE CASCADE
            );
CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entry_id INTEGER,
                xp INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE SET NULL
            );

CREATE TABLE asset_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                mission_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES daily_missions(id) ON DELETE SET NULL
            );

CREATE TABLE career_moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                moment_type TEXT NOT NULL DEFAULT 'decision',
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                project_id INTEGER,
                occurred_on TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES research_projects(id) ON DELETE SET NULL
            );

CREATE TABLE daily_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                day_index INTEGER NOT NULL,
                mood TEXT NOT NULL DEFAULT 'steady',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(plan_id, day_index),
                FOREIGN KEY(plan_id) REFERENCES study_plans(id) ON DELETE CASCADE
            );

CREATE TABLE daily_missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                day_index INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT '主线',
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                deliverable TEXT NOT NULL DEFAULT '',
                duration_minutes INTEGER NOT NULL DEFAULT 30,
                xp INTEGER NOT NULL DEFAULT 10,
                optional INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                xp_awarded INTEGER NOT NULL DEFAULT 0,
                quest_id INTEGER,
                completed_at TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, track_id INTEGER, workspace_id INTEGER, project_id INTEGER, stones_awarded INTEGER NOT NULL DEFAULT 0, materials_awarded INTEGER NOT NULL DEFAULT 0, postponed_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(plan_id) REFERENCES study_plans(id) ON DELETE CASCADE
            );

CREATE TABLE easter_eggs (
                egg_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                unlocked INTEGER NOT NULL DEFAULT 0,
                discovered_at TEXT
            );

CREATE TABLE entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'document',
                domain TEXT NOT NULL DEFAULT '未分类',
                tags TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                file_path TEXT,
                original_name TEXT,
                mime_type TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                dataset_rows INTEGER,
                dataset_columns INTEGER,
                dataset_schema TEXT NOT NULL DEFAULT '[]',
                dataset_preview TEXT NOT NULL DEFAULT '[]',
                favorite INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT '',
                workspace_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            , analysis_json TEXT NOT NULL DEFAULT '{}', indexed_at TEXT, extract_status TEXT NOT NULL DEFAULT 'pending', content_format TEXT NOT NULL DEFAULT 'plain');

CREATE TABLE experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id TEXT NOT NULL UNIQUE,
                experiment_date TEXT,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                conditions TEXT NOT NULL DEFAULT '',
                measurements TEXT NOT NULL DEFAULT '',
                eg_content REAL,
                eg_unit TEXT NOT NULL DEFAULT 'wt%',
                water_cement_ratio REAL,
                compaction_pressure REAL,
                compaction_unit TEXT NOT NULL DEFAULT 'MPa',
                thickness_cm REAL,
                area_cm2 REAL,
                electrolyte TEXT NOT NULL DEFAULT '',
                voltage_min REAL,
                voltage_max REAL,
                scan_rate REAL,
                scan_rate_unit TEXT NOT NULL DEFAULT 'mV/s',
                specific_capacitance REAL,
                capacitance_unit TEXT NOT NULL DEFAULT 'F/g',
                conductivity REAL,
                conductivity_unit TEXT NOT NULL DEFAULT 'S/m',
                compressive_strength REAL,
                strength_unit TEXT NOT NULL DEFAULT 'MPa',
                hypothesis TEXT NOT NULL DEFAULT '',
                observations TEXT NOT NULL DEFAULT '',
                conclusion TEXT NOT NULL DEFAULT '',
                next_step TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                attachment_entry_id INTEGER,
                workspace_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(attachment_entry_id) REFERENCES entries(id) ON DELETE SET NULL
            );

CREATE TABLE herb_inventory (
                grade INTEGER PRIMARY KEY,
                herb_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

CREATE TABLE inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL UNIQUE,
                item_type TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                level INTEGER NOT NULL DEFAULT 1,
                equipped INTEGER NOT NULL DEFAULT 0,
                acquired_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE mission_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                storage_key TEXT NOT NULL UNIQUE,
                file_count INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, review_text TEXT NOT NULL DEFAULT '', review_source TEXT NOT NULL DEFAULT 'manual',
                FOREIGN KEY(mission_id) REFERENCES daily_missions(id) ON DELETE CASCADE
            );

CREATE TABLE mission_delivery_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(delivery_id) REFERENCES mission_deliveries(id) ON DELETE CASCADE
            );

CREATE TABLE online_sync_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE online_sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                synced_at TEXT
            , schema_version INTEGER NOT NULL DEFAULT 1, aggregate_type TEXT NOT NULL DEFAULT '', aggregate_id TEXT NOT NULL DEFAULT '', sequence_no INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, dead_letter INTEGER NOT NULL DEFAULT 0);

CREATE TABLE player_profile (
                id INTEGER PRIMARY KEY CHECK (id=1),
                display_name TEXT NOT NULL DEFAULT '修士',
                title TEXT NOT NULL DEFAULT '水泥基能源材料探索者',
                bio TEXT NOT NULL DEFAULT '',
                skills TEXT NOT NULL DEFAULT '',
                capabilities TEXT NOT NULL DEFAULT '',
                goals TEXT NOT NULL DEFAULT '',
                avatar_symbol TEXT NOT NULL DEFAULT '道',
                featured_item_key TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

CREATE TABLE project_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'Crossref',
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                authors TEXT NOT NULL DEFAULT '',
                publication_year INTEGER,
                source TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                cited_by INTEGER NOT NULL DEFAULT 0,
                relation TEXT NOT NULL DEFAULT 'unclassified',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(project_id,provider,external_id),
                FOREIGN KEY(project_id) REFERENCES research_projects(id) ON DELETE CASCADE
            );

CREATE TABLE project_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                stage_key TEXT NOT NULL DEFAULT 'custom',
                title TEXT NOT NULL,
                criterion TEXT NOT NULL DEFAULT '',
                deliverable TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                due_date TEXT,
                evidence TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES research_projects(id) ON DELETE CASCADE
            );

CREATE TABLE project_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                update_type TEXT NOT NULL DEFAULT 'checkin',
                summary TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                confidence INTEGER NOT NULL DEFAULT 50,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES research_projects(id) ON DELETE CASCADE
            );

CREATE TABLE project_workspaces (
                project_id INTEGER NOT NULL,
                workspace_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,workspace_id),
                FOREIGN KEY(project_id) REFERENCES research_projects(id) ON DELETE CASCADE,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );

CREATE TABLE quests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                xp INTEGER NOT NULL DEFAULT 10,
                completed INTEGER NOT NULL DEFAULT 0,
                recurring TEXT NOT NULL DEFAULT 'once',
                due_date TEXT,
                deliverable TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                difficulty INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'planned',
                workspace_id INTEGER,
                xp_awarded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT
            );

CREATE TABLE realm_tribulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_key TEXT NOT NULL,
                from_stage_key TEXT NOT NULL,
                to_stage_key TEXT NOT NULL,
                session_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                score INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES review_sessions(id) ON DELETE CASCADE
            );

CREATE TABLE research_folder_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(folder_id) REFERENCES research_folders(id) ON DELETE CASCADE
            );

CREATE TABLE research_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                storage_key TEXT NOT NULL UNIQUE,
                file_count INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES research_tracks(id) ON DELETE SET NULL
            );


CREATE TABLE research_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                research_question TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                target_outcome TEXT NOT NULL DEFAULT '',
                success_criteria TEXT NOT NULL DEFAULT '',
                current_state TEXT NOT NULL DEFAULT '',
                constraints_text TEXT NOT NULL DEFAULT '',
                search_query TEXT NOT NULL DEFAULT '',
                workspace_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                target_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL
            );


CREATE TABLE review_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question_index INTEGER NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                level TEXT NOT NULL DEFAULT 'needs_review',
                feedback TEXT NOT NULL DEFAULT '',
                evidence_quote TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '',
                self_rating TEXT NOT NULL DEFAULT '',
                next_due TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, question_index),
                FOREIGN KEY(session_id) REFERENCES review_sessions(id) ON DELETE CASCADE
            );

CREATE TABLE review_session_sources (
                session_id INTEGER NOT NULL,
                review_source_id INTEGER NOT NULL,
                PRIMARY KEY(session_id, review_source_id),
                FOREIGN KEY(session_id) REFERENCES review_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(review_source_id) REFERENCES review_sources(id) ON DELETE CASCADE
            );

CREATE TABLE review_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL DEFAULT 'yesterday',
                title TEXT NOT NULL,
                source_date TEXT NOT NULL DEFAULT '',
                questions_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                provider TEXT NOT NULL DEFAULT '离线规则',
                fallback_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

CREATE TABLE review_snoozes (
                review_day TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

CREATE TABLE review_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source_text TEXT NOT NULL,
                storage_key TEXT NOT NULL DEFAULT '',
                source_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_type, source_id)
            );

CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

CREATE TABLE simulation_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                simulation_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'other',
                file_path TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(simulation_id) REFERENCES simulations(id) ON DELETE CASCADE
            );

CREATE TABLE simulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_name TEXT NOT NULL,
                project_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'NEW',
                engine TEXT NOT NULL DEFAULT 'LAMMPS',
                engine_version TEXT NOT NULL DEFAULT '',
                ensemble TEXT NOT NULL DEFAULT '',
                forcefield TEXT NOT NULL DEFAULT '',
                atoms INTEGER,
                steps INTEGER,
                temperature REAL,
                timestep REAL,
                last_step INTEGER,
                last_temp REAL,
                last_etotal REAL,
                warnings INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                run_command TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                folder_path TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL DEFAULT '{}',
                workspace_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE special_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                deliverable TEXT NOT NULL DEFAULT '',
                why_it_matters TEXT NOT NULL DEFAULT '',
                difficulty INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'offered',
                provider TEXT NOT NULL DEFAULT '离线规则',
                fallback_reason TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                review_text TEXT NOT NULL DEFAULT '',
                storage_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                completed_at TEXT
            );

CREATE TABLE study_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                current_day INTEGER NOT NULL DEFAULT 1,
                total_days INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'active',
                source_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );


CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                icon TEXT NOT NULL DEFAULT '研',
                module TEXT NOT NULL DEFAULT 'knowledge',
                description TEXT NOT NULL DEFAULT '',
                accent TEXT NOT NULL DEFAULT 'clay',
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                pinned_home INTEGER NOT NULL DEFAULT 0,
                objective TEXT NOT NULL DEFAULT '',
                workflow_json TEXT NOT NULL DEFAULT '[]',
                toolset_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE INDEX idx_activities_created ON activities(created_at DESC);

CREATE INDEX idx_asset_transactions_key ON asset_transactions(asset_key, created_at DESC);

CREATE INDEX idx_career_moments_date ON career_moments(occurred_on DESC, id DESC);

CREATE INDEX idx_career_moments_project ON career_moments(project_id, occurred_on DESC);

CREATE INDEX idx_daily_missions_plan_day ON daily_missions(plan_id, day_index, sort_order);

CREATE INDEX idx_daily_missions_project ON daily_missions(project_id,completed,day_index);

CREATE INDEX idx_daily_missions_workspace ON daily_missions(workspace_id,completed,day_index);

CREATE INDEX idx_delivery_files_delivery ON mission_delivery_files(delivery_id, relative_path);

CREATE INDEX idx_entries_domain ON entries(domain);

CREATE INDEX idx_entries_kind ON entries(kind);

CREATE INDEX idx_entries_updated ON entries(updated_at DESC);

CREATE INDEX idx_entries_workspace ON entries(workspace_id, updated_at DESC);

CREATE INDEX idx_experiments_date ON experiments(experiment_date DESC);

CREATE INDEX idx_experiments_status ON experiments(status);

CREATE INDEX idx_experiments_workspace ON experiments(workspace_id, updated_at DESC);

CREATE INDEX idx_mission_deliveries_mission ON mission_deliveries(mission_id, created_at DESC);

CREATE INDEX idx_online_sync_status ON online_sync_queue(status, created_at);

CREATE INDEX idx_project_cases_project ON project_cases(project_id, created_at DESC);

CREATE INDEX idx_project_milestones_project ON project_milestones(project_id, sort_order, id);

CREATE INDEX idx_project_updates_project ON project_updates(project_id, created_at DESC);

CREATE INDEX idx_project_workspaces_workspace ON project_workspaces(workspace_id,project_id);

CREATE INDEX idx_quests_workspace ON quests(workspace_id, completed, updated_at DESC);

CREATE INDEX idx_realm_tribulations_gate ON realm_tribulations(gate_key, status, id);

CREATE INDEX idx_research_folder_files_folder ON research_folder_files(folder_id, relative_path);

CREATE INDEX idx_research_folders_track ON research_folders(track_id, updated_at DESC);

CREATE INDEX idx_research_plan_track ON research_plan_items(track_id, sort_order, id);

CREATE INDEX idx_research_projects_status ON research_projects(status, updated_at DESC);

CREATE INDEX idx_research_tracks_order ON research_tracks(sort_order, id);

CREATE INDEX idx_review_answers_due ON review_answers(next_due, self_rating);

CREATE INDEX idx_review_sessions_status ON review_sessions(status, created_at DESC);

CREATE INDEX idx_review_sources_date ON review_sources(source_date DESC, id);

CREATE INDEX idx_simulations_updated ON simulations(updated_at DESC);

CREATE INDEX idx_simulations_workspace ON simulations(workspace_id, updated_at DESC);

CREATE INDEX idx_special_tasks_status ON special_tasks(status, created_at DESC);

CREATE INDEX idx_study_plans_status ON study_plans(status, updated_at DESC);

CREATE INDEX idx_workspaces_order ON workspaces(active, sort_order, id);

CREATE TABLE IF NOT EXISTS user_feedback(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'bug',
                content TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                hub_synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);


-- 1.0 索引：按真实查询路径建立，避免全表扫描
CREATE INDEX IF NOT EXISTS idx_entries_status_workspace ON entries(status, workspace_id);
CREATE INDEX IF NOT EXISTS idx_entries_kind_status ON entries(kind, status);
CREATE INDEX IF NOT EXISTS idx_entries_updated ON entries(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_entries_domain ON entries(domain);
CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activities_action ON activities(action);
CREATE INDEX IF NOT EXISTS idx_missions_plan_day ON daily_missions(plan_id, day_index);
CREATE INDEX IF NOT EXISTS idx_missions_pending ON daily_missions(completed, plan_id);
CREATE INDEX IF NOT EXISTS idx_review_sources_date ON review_sources(source_date DESC);
CREATE INDEX IF NOT EXISTS idx_review_answers_due ON review_answers(next_due);
CREATE INDEX IF NOT EXISTS idx_deliveries_mission ON mission_deliveries(mission_id);
CREATE INDEX IF NOT EXISTS idx_delivery_files_delivery ON mission_delivery_files(delivery_id);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON project_milestones(project_id, status, sort_order);
CREATE INDEX IF NOT EXISTS idx_project_updates_project ON project_updates(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_career_moments_project ON career_moments(project_id, occurred_on DESC);
CREATE INDEX IF NOT EXISTS idx_queue_status ON online_sync_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_workspaces_active ON workspaces(active, sort_order);
CREATE INDEX IF NOT EXISTS idx_quests_open ON quests(completed, status);
CREATE INDEX IF NOT EXISTS idx_experiments_workspace ON experiments(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulations_workspace ON simulations(workspace_id, updated_at DESC);
"""

FTS_SCHEMA_SQL = r"""CREATE VIRTUAL TABLE entries_fts USING fts5(
                    title, summary, content, tags, domain,
                    content='entries', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );

CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, title, summary, content, tags, domain)
                    VALUES ('delete', old.id, old.title, old.summary, old.content, old.tags, old.domain);
                END;

CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
                    INSERT INTO entries_fts(rowid, title, summary, content, tags, domain)
                    VALUES (new.id, new.title, new.summary, new.content, new.tags, new.domain);
                END;

CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, title, summary, content, tags, domain)
                    VALUES ('delete', old.id, old.title, old.summary, old.content, old.tags, old.domain);
                    INSERT INTO entries_fts(rowid, title, summary, content, tags, domain)
                    VALUES (new.id, new.title, new.summary, new.content, new.tags, new.domain);
                END;
"""
