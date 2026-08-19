from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "herdr_agents_under_test", str(ROOT / "herdr-agents")
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AGENTS = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AGENTS)


class CodexForkIndexTests(unittest.TestCase):
    def write_rollout(self, root: Path, session_id: str, payload: dict) -> str:
        folder = root / "2026" / "08" / "19"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-now-{session_id}.jsonl"
        record = {"type": "session_meta", "payload": {"id": session_id, **payload}}
        path.write_text(json.dumps(record) + "\n")
        return str(path)

    def test_index_keeps_user_forks_and_hides_subagent_edges(self):
        parent = "01a01c11-5a26-7db2-befe-ffd79c3084ac"
        child = "01a01c11-eb5c-7c91-ad43-ac31378debbd"
        worker = "01a01c12-1111-7222-8333-444444444444"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_rollout(
                root,
                parent,
                {"cwd": "/repo", "source": "cli", "thread_source": "user"},
            )
            self.write_rollout(
                root,
                child,
                {
                    "forked_from_id": parent,
                    "cwd": "/repo",
                    "source": "cli",
                    "thread_source": "user",
                },
            )
            self.write_rollout(
                root,
                worker,
                {
                    "forked_from_id": parent,
                    "parent_thread_id": parent,
                    "cwd": "/repo",
                    "source": {"subagent": {"thread_id": parent}},
                },
            )
            with mock.patch.object(AGENTS, "CODEX_SESSIONS", tmp):
                sessions = AGENTS.codex_session_index()

        self.assertEqual(sessions[child]["forked_from_id"], parent)
        self.assertEqual(sessions[worker]["forked_from_id"], "")
        self.assertNotIn(worker, AGENTS.codex_fork_graph(sessions))

    def test_collect_prefers_title_during_reporter_lag_and_owns_parent(self):
        parent = "01a01c11-5a26-7db2-befe-ffd79c3084ac"
        child = "01a01c11-eb5c-7c91-ad43-ac31378debbd"
        sessions = {
            parent: {
                "id": parent,
                "forked_from_id": "",
                "cwd": "/repo",
                "path": "/rollouts/parent.jsonl",
            },
            child: {
                "id": child,
                "forked_from_id": parent,
                "cwd": "/repo",
                "path": "/rollouts/child.jsonl",
            },
        }
        pane = {
            "pane_id": "w1:p1",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "cwd": "/repo",
            "terminal_title_stripped": f"repo | {child} | main",
            "agent_session": {
                "agent": "codex",
                "kind": "id",
                "value": parent,
            },
        }

        def herdr_json(*args):
            if args[:2] == ("pane", "list"):
                return {"panes": [pane]}
            if args[:2] == ("workspace", "list"):
                return {
                    "workspaces": [{"workspace_id": "w1", "number": 7, "label": "repo"}]
                }
            if args[:2] == ("pane", "process-info"):
                return {"process_info": {"foreground_processes": [{"pid": 1234}]}}
            return {}

        with (
            mock.patch.object(AGENTS, "herdr_json", side_effect=herdr_json),
            mock.patch.object(AGENTS, "codex_session_index", return_value=sessions),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "codex_lock_pids", return_value={1234}),
            mock.patch.object(AGENTS, "resolve_state", return_value="idle"),
        ):
            rows = AGENTS.collect()

        live = next(row for row in rows if row["row_kind"] == "live")
        branch = next(row for row in rows if row["row_kind"] == "codex_branch")
        self.assertEqual(live["session_id"], child)
        self.assertEqual(live["reported_session_id"], parent)
        self.assertEqual(live["transcript"], "/rollouts/child.jsonl")
        self.assertEqual(branch["session_id"], parent)
        self.assertEqual(branch["owner_pane_id"], "w1:p1")
        self.assertEqual(branch["key"], AGENTS.CODEX_KEY + parent)

    def test_single_related_pane_does_not_claim_external_writer_without_lsof(self):
        parent = "01a01c11-5a26-7db2-befe-ffd79c3084ac"
        child = "01a01c11-eb5c-7c91-ad43-ac31378debbd"
        sessions = {
            parent: {
                "id": parent,
                "forked_from_id": "",
                "cwd": "/repo",
                "path": "/rollouts/parent.jsonl",
            },
            child: {
                "id": child,
                "forked_from_id": parent,
                "cwd": "/repo",
                "path": "/rollouts/child.jsonl",
            },
        }
        live = {
            "key": "w1:p1",
            "row_kind": "live",
            "session_id": child,
            "reported_session_id": child,
            "pane_id": "w1:p1",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "agent": "codex",
            "state": "idle",
            "number": 1,
            "label": "repo",
            "cwd": "/repo",
        }
        with (
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "codex_lock_pids", return_value=None),
        ):
            branches = AGENTS.codex_branch_rows(
                [live], sessions, AGENTS.codex_fork_graph(sessions)
            )

        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["session_id"], parent)
        self.assertEqual(branches[0]["owner_pane_id"], "")
        self.assertIn("open outside herdr", branches[0]["title"])

    def test_stale_reporter_is_direct_owner_evidence_without_lsof(self):
        parent = "01a01c11-5a26-7db2-befe-ffd79c3084ac"
        child = "01a01c11-eb5c-7c91-ad43-ac31378debbd"
        sessions = {
            parent: {
                "id": parent,
                "forked_from_id": "",
                "cwd": "/repo",
                "path": "/rollouts/parent.jsonl",
            },
            child: {
                "id": child,
                "forked_from_id": parent,
                "cwd": "/repo",
                "path": "/rollouts/child.jsonl",
            },
        }
        live = {
            "key": "w1:p1",
            "row_kind": "live",
            "session_id": child,
            "reported_session_id": parent,
            "pane_id": "w1:p1",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "agent": "codex",
            "state": "idle",
            "number": 1,
            "label": "repo",
            "cwd": "/repo",
        }
        with (
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "codex_lock_pids", return_value=None),
        ):
            branches = AGENTS.codex_branch_rows(
                [live], sessions, AGENTS.codex_fork_graph(sessions)
            )

        self.assertEqual(branches[0]["session_id"], parent)
        self.assertEqual(branches[0]["owner_pane_id"], "w1:p1")


class CodexBranchActivationTests(unittest.TestCase):
    SESSION_ID = "01a01c11-eb5c-7c91-ad43-ac31378debbd"

    def row(self, *, active: bool, owner: str = "") -> dict:
        return {
            "key": AGENTS.CODEX_KEY + self.SESSION_ID,
            "row_kind": "codex_branch",
            "session_id": self.SESSION_ID,
            "active_writer": active,
            "owner_pane_id": owner,
            "pane_id": owner,
            "workspace_id": "w1" if owner else "",
            "tab_id": "w1:t1" if owner else "",
            "cwd": "/repo",
        }

    def test_owned_branch_switches_inside_the_existing_tui(self):
        row = self.row(active=True, owner="w1:p1")
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with (
            mock.patch.object(AGENTS, "collect", return_value=[row]),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "herdr_run", return_value=completed) as run,
            mock.patch.object(AGENTS, "focus", return_value=0) as focus,
        ):
            result = AGENTS.activate_codex_branch(row)

        self.assertEqual(result, 0)
        run.assert_called_once_with(
            "agent",
            "prompt",
            "w1:p1",
            f"/resume {self.SESSION_ID}",
            timeout=10,
        )
        focus.assert_called_once_with(row)

    def test_dormant_branch_starts_codex_resume_in_a_new_workspace(self):
        row = self.row(active=False)
        with (
            mock.patch.object(AGENTS, "collect", return_value=[row]),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=False),
            mock.patch.object(AGENTS, "launch", return_value=0) as launch,
        ):
            result = AGENTS.activate_codex_branch(row)

        self.assertEqual(result, 0)
        launch.assert_called_once_with(
            "/repo", "codex", agent_args=("resume", self.SESSION_ID)
        )

    def test_unmapped_active_writer_is_not_duplicated(self):
        row = self.row(active=True)
        with (
            mock.patch.object(AGENTS, "collect", return_value=[row]),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "herdr", return_value="") as herdr,
            mock.patch.object(AGENTS, "launch") as launch,
        ):
            result = AGENTS.activate_codex_branch(row)

        self.assertEqual(result, 1)
        launch.assert_not_called()
        self.assertEqual(
            herdr.call_args.args[:3],
            ("notification", "show", "Codex branch is already open"),
        )

    def test_start_agent_places_resume_args_after_separator(self):
        completed = subprocess.CompletedProcess([], 0, stdout="started", stderr="")
        with mock.patch.object(AGENTS, "herdr_run", return_value=completed) as run:
            started, why = AGENTS.start_agent(
                "repo-codex",
                "codex",
                "w1:p1",
                agent_args=("resume", self.SESSION_ID),
            )

        self.assertEqual((started, why), ("started", ""))
        run.assert_called_once_with(
            "agent",
            "start",
            "repo-codex",
            "--kind",
            "codex",
            "--pane",
            "w1:p1",
            "--timeout",
            "60000",
            "--",
            "resume",
            self.SESSION_ID,
            timeout=90,
        )


if __name__ == "__main__":
    unittest.main()
