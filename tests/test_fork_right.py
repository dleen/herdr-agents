from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "herdr_agents_fork_right_under_test", str(ROOT / "herdr-agents")
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
AGENTS = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AGENTS)

PARENT_ID = "01a01c59-b202-7b63-8daa-11e7ea356225"
CHILD_ID = "01a01c60-1111-7222-8333-444444444444"
PARENT_PANE = "w1:p1"
CHILD_PANE = "w1:p2"
CWD = "/repo"


def completed(*, stdout: str = "{}", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def resolved_parent(**changes) -> dict:
    parent = {
        "pane_id": PARENT_PANE,
        "session_id": PARENT_ID,
        "pid": 101,
        "cwd": CWD,
        "cwd_real": CWD,
        "meta": {"id": PARENT_ID, "forked_from_id": "", "cwd": CWD},
    }
    parent.update(changes)
    return parent


class LocalCodexPaneTests(unittest.TestCase):
    def pane(self, *, title_id: str = PARENT_ID, foreground_cwd: str = CWD) -> dict:
        return {
            "pane_id": PARENT_PANE,
            "agent": "codex",
            "agent_session": {
                "agent": "codex",
                "kind": "id",
                "value": PARENT_ID,
            },
            "cwd": CWD,
            "foreground_cwd": foreground_cwd,
            "terminal_title_stripped": f"repo | {title_id} | main",
        }

    def process(self, *, name: str = "codex", cwd: str = CWD) -> dict:
        return {
            "pid": 101,
            "name": name,
            "argv0": name,
            "argv": [name],
            "cwd": cwd,
        }

    def herdr_json(self, pane: dict, process: dict):
        def response(*args):
            if args[:2] == ("pane", "get"):
                return {"pane": pane}
            if args[:2] == ("pane", "process-info"):
                return {
                    "process_info": {
                        "pane_id": PARENT_PANE,
                        "shell_pid": 99,
                        "foreground_processes": [process],
                    }
                }
            return {}

        return response

    def resolve(self, pane: dict, process: dict, *, meta_cwd: str = CWD,
                lock_pids: set[int] | None = None):
        meta = {"id": PARENT_ID, "forked_from_id": "", "cwd": meta_cwd}
        with (
            mock.patch.object(
                AGENTS, "herdr_json", side_effect=self.herdr_json(pane, process)
            ),
            mock.patch.object(AGENTS, "codex_session_by_id", return_value=meta),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(
                AGENTS, "codex_lock_pids",
                return_value={101} if lock_pids is None else lock_pids,
            ),
        ):
            return AGENTS.local_codex_pane(PARENT_PANE)

    def test_resolves_only_when_reporter_title_rollout_process_cwd_and_lock_agree(self):
        parent, why = self.resolve(self.pane(), self.process())

        self.assertEqual(why, "")
        self.assertEqual(parent["session_id"], PARENT_ID)
        self.assertEqual(parent["pid"], 101)
        self.assertEqual(parent["cwd"], CWD)

    def test_rejects_reporter_title_disagreement(self):
        parent, why = self.resolve(self.pane(title_id=CHILD_ID), self.process())

        self.assertIsNone(parent)
        self.assertIn("title", why)

    def test_rejects_cloud_attach_instead_of_local_codex(self):
        parent, why = self.resolve(self.pane(), self.process(name="ssh"))

        self.assertIsNone(parent)
        self.assertIn("not local Codex", why)

    def test_rejects_cwd_or_lock_owner_disagreement(self):
        cases = (
            ("rollout cwd", {"meta_cwd": "/other"}, "working directories"),
            ("multiple lock owners", {"lock_pids": {101, 202}}, "uniquely own"),
            ("unobservable lock owners", {"lock_pids": set()}, "uniquely own"),
        )
        for label, kwargs, expected in cases:
            with self.subTest(label=label):
                parent, why = self.resolve(self.pane(), self.process(), **kwargs)
                self.assertIsNone(parent)
                self.assertIn(expected, why)

    def test_rejects_when_lsof_is_unavailable(self):
        pane, process = self.pane(), self.process()
        meta = {"id": PARENT_ID, "forked_from_id": "", "cwd": CWD}
        with (
            mock.patch.object(
                AGENTS, "herdr_json", side_effect=self.herdr_json(pane, process)
            ),
            mock.patch.object(AGENTS, "codex_session_by_id", return_value=meta),
            mock.patch.object(AGENTS, "codex_writer_active", return_value=True),
            mock.patch.object(AGENTS, "codex_lock_pids", return_value=None),
        ):
            parent, why = AGENTS.local_codex_pane(PARENT_PANE)

        self.assertIsNone(parent)
        self.assertIn("lsof", why)


class ForkChildVerificationTests(unittest.TestCase):
    def test_requires_distinct_processes_and_the_exact_fork_edge(self):
        parent = resolved_parent()
        child = {
            "pane_id": CHILD_PANE,
            "session_id": CHILD_ID,
            "pid": 202,
            "cwd": CWD,
            "cwd_real": CWD,
            "meta": {"id": CHILD_ID, "forked_from_id": PARENT_ID, "cwd": CWD},
        }
        with mock.patch.object(
            AGENTS, "local_codex_pane", side_effect=[(parent, ""), (child, "")]
        ):
            child_id, why = AGENTS.verify_fork_child(parent, CHILD_PANE)

        self.assertEqual((child_id, why), (CHILD_ID, ""))

    def test_rejects_same_writer_or_wrong_parent(self):
        cases = (
            (101, PARENT_ID, "same process"),
            (202, CHILD_ID, "requested parent"),
        )
        for pid, forked_from_id, expected in cases:
            parent = resolved_parent()
            child = {
                "pane_id": CHILD_PANE,
                "session_id": CHILD_ID,
                "pid": pid,
                "cwd": CWD,
                "cwd_real": CWD,
                "meta": {
                    "id": CHILD_ID,
                    "forked_from_id": forked_from_id,
                    "cwd": CWD,
                },
            }
            with self.subTest(pid=pid, forked_from_id=forked_from_id):
                with mock.patch.object(
                    AGENTS, "local_codex_pane",
                    side_effect=[(parent, ""), (child, "")],
                ):
                    child_id, why = AGENTS.verify_fork_child(parent, CHILD_PANE)
                self.assertEqual(child_id, "")
                self.assertIn(expected, why)


class ForkRightWorkflowTests(unittest.TestCase):
    def split_result(self):
        payload = {"result": {"pane": {"pane_id": CHILD_PANE}}}
        return completed(stdout=json.dumps(payload))

    def herdr_run(self, *args, **_kwargs):
        if args[:2] == ("pane", "split"):
            return self.split_result()
        if args[:2] == ("agent", "focus"):
            return completed()
        self.fail(f"unexpected herdr command: {args}")

    def test_success_splits_without_focus_starts_codex_fork_then_focuses_child(self):
        parent = resolved_parent()
        with (
            mock.patch.object(
                AGENTS, "local_codex_pane",
                side_effect=[(parent, ""), (parent, "")],
            ),
            mock.patch.object(AGENTS, "herdr_run", side_effect=self.herdr_run) as run,
            mock.patch.object(AGENTS, "unique_agent_name", return_value="repo-codex"),
            mock.patch.object(AGENTS, "start_agent", return_value=("started", "")) as start,
            mock.patch.object(
                AGENTS, "wait_for_fork_child", return_value=(CHILD_ID, "")
            ),
            mock.patch.object(AGENTS, "close_created_empty_pane") as close,
        ):
            result = AGENTS.fork_right_locked(PARENT_PANE)

        self.assertEqual(result, 0)
        split_args = run.call_args_list[0].args
        self.assertEqual(split_args[:4], ("pane", "split", "--pane", PARENT_PANE))
        self.assertIn("--no-focus", split_args)
        self.assertEqual(split_args[split_args.index("--direction") + 1], "right")
        self.assertEqual(split_args[split_args.index("--cwd") + 1], CWD)
        start.assert_called_once_with(
            "repo-codex", "codex", CHILD_PANE,
            agent_args=("fork", PARENT_ID),
        )
        self.assertEqual(run.call_args_list[-1].args[:3], ("agent", "focus", CHILD_PANE))
        close.assert_not_called()
        self.assertEqual(parent["pid"], 101)

    def test_parent_change_after_split_closes_only_the_created_empty_pane(self):
        parent = resolved_parent()
        changed = resolved_parent(session_id=CHILD_ID)
        with (
            mock.patch.object(
                AGENTS, "local_codex_pane",
                side_effect=[(parent, ""), (changed, "")],
            ),
            mock.patch.object(AGENTS, "herdr_run", side_effect=self.herdr_run),
            mock.patch.object(AGENTS, "close_created_empty_pane", return_value=True) as close,
            mock.patch.object(AGENTS, "start_agent") as start,
            mock.patch.object(AGENTS, "fork_notice"),
        ):
            result = AGENTS.fork_right_locked(PARENT_PANE)

        self.assertEqual(result, 1)
        close.assert_called_once_with(CHILD_PANE)
        start.assert_not_called()
        self.assertEqual(parent["pid"], 101)

    def test_split_failure_does_not_touch_or_restart_the_parent(self):
        parent = resolved_parent()
        failed = completed(stderr="split unavailable", returncode=1)
        with (
            mock.patch.object(
                AGENTS, "local_codex_pane", return_value=(parent, "")
            ),
            mock.patch.object(AGENTS, "herdr_run", return_value=failed),
            mock.patch.object(AGENTS, "start_agent") as start,
            mock.patch.object(AGENTS, "close_created_empty_pane") as close,
            mock.patch.object(AGENTS, "fork_notice"),
        ):
            result = AGENTS.fork_right_locked(PARENT_PANE)

        self.assertEqual(result, 1)
        start.assert_not_called()
        close.assert_not_called()
        self.assertEqual(parent["pid"], 101)

    def test_start_failure_closes_only_when_the_new_pane_is_proven_empty(self):
        for empty, expected_cleanup in ((True, "closed"), (False, "left visible")):
            parent = resolved_parent()
            with self.subTest(empty=empty):
                with (
                    mock.patch.object(
                        AGENTS, "local_codex_pane",
                        side_effect=[(parent, ""), (parent, "")],
                    ),
                    mock.patch.object(AGENTS, "herdr_run", side_effect=self.herdr_run),
                    mock.patch.object(AGENTS, "unique_agent_name", return_value="repo-codex"),
                    mock.patch.object(
                        AGENTS, "start_agent", return_value=("", "agent start failed")
                    ),
                    mock.patch.object(
                        AGENTS, "close_created_empty_pane", return_value=empty
                    ),
                    mock.patch.object(AGENTS, "fork_notice") as notice,
                ):
                    result = AGENTS.fork_right_locked(PARENT_PANE)

                self.assertEqual(result, 1)
                self.assertIn(expected_cleanup, notice.call_args.args[-1])
                self.assertEqual(parent["pid"], 101)

    def test_ambiguous_child_is_left_visible_and_parent_is_never_restarted(self):
        parent = resolved_parent()
        with (
            mock.patch.object(
                AGENTS, "local_codex_pane",
                side_effect=[(parent, ""), (parent, "")],
            ),
            mock.patch.object(AGENTS, "herdr_run", side_effect=self.herdr_run),
            mock.patch.object(AGENTS, "unique_agent_name", return_value="repo-codex"),
            mock.patch.object(AGENTS, "start_agent", return_value=("started", "")),
            mock.patch.object(
                AGENTS, "wait_for_fork_child",
                return_value=("", "child lock owner did not settle"),
            ),
            mock.patch.object(AGENTS, "close_created_empty_pane") as close,
            mock.patch.object(AGENTS, "fork_notice") as notice,
        ):
            result = AGENTS.fork_right_locked(PARENT_PANE)

        self.assertEqual(result, 1)
        close.assert_not_called()
        self.assertIn("left visible", notice.call_args.args[-1])
        self.assertEqual(parent["pid"], 101)


class ForkRightCleanupAndLockTests(unittest.TestCase):
    def test_empty_pane_requires_an_idle_shell_and_no_agent(self):
        pane = {"pane_id": CHILD_PANE, "agent": None, "agent_session": None}

        def herdr_json(*args):
            if args[:2] == ("pane", "get"):
                return {"pane": pane}
            return {
                "process_info": {
                    "pane_id": CHILD_PANE,
                    "shell_pid": 303,
                    "foreground_processes": [],
                }
            }

        with mock.patch.object(AGENTS, "herdr_json", side_effect=herdr_json):
            self.assertTrue(AGENTS.pane_provably_empty(CHILD_PANE))

        pane["agent_session"] = {"agent": "codex"}
        with mock.patch.object(AGENTS, "herdr_json", side_effect=herdr_json):
            self.assertFalse(AGENTS.pane_provably_empty(CHILD_PANE))

    def test_second_invocation_is_refused_by_the_per_pane_lock(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"HERDR_PANE_ID": PARENT_PANE}),
            mock.patch.object(AGENTS, "FORK_LOCK_DIR", tmp),
            mock.patch.object(AGENTS.fcntl, "lockf", side_effect=BlockingIOError),
            mock.patch.object(AGENTS, "fork_right_locked") as locked,
            mock.patch.object(AGENTS, "fork_notice") as notice,
        ):
            result = AGENTS.fork_right()

        self.assertEqual(result, 1)
        locked.assert_not_called()
        self.assertIn("already starting", notice.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
