# herdr-agents

An fzf picker for every agent pane [herdr](https://herdr.dev) knows about —
bound to a key, it opens as a popup, lists your agents worst-first, previews
what each one has actually been doing, and starts new ones in folders that
have nothing running. A second action forks the active Codex session into a
new pane on its right. Installs as a herdr plugin:

```sh
herdr plugin install dleen/herdr-agents
```

```
agent> ▏
  ~/code/service-a  (2)
    !  3  needs input   12s  3h   claude  waiting on approval to force-push
    *  1  working        2s  20m  pi      refactoring the retry loop
  ~/code/notes  (1)
    o  4  idle           8m   2d  codex   —
    ↳  4  branch         9m   2d  codex   fork parent · switch in 4 · 01a0…
  ~/code/infra  + pi
```

The two age columns are **how long since it last did anything** and **how long
the session has existed** — the second dimmed, because it never changes what
you do next, only whether you are looking at something opened after lunch or
something that has been grinding since Tuesday.

Herdr's own sidebar agents panel is a ~30-column rail, and it lists only the
panes whose agent state herdr managed to detect — a pane whose agent process it
never classified is missing entirely. This reads `herdr pane list` instead and
resolves state itself, so those panes show up, in a popup with room for a real
preview.

## What it does

- **One row per agent pane, plus saved Codex fork branches**, grouped by working
  directory (full `cwd`, so two worktrees of the same repo stay distinct) and
  sorted worst-first: blocked →
  working → idle/done → unknown, then most recently active first. A folder with
  something waiting on a human floats to the top of the list; state alone stops
  sorting anything once a long session's agents are all idle, which is most of
  them most of the time.
- **`/fork` stays selectable without a second writer.** The picker reads the
  rollout headers' `id`, `forked_from_id`, and `cwd` fields and shows the saved
  peers of every live Codex session. If the branch's writer lock belongs to a
  live Herdr pane, enter sends the supported `/resume <id>` command to that
  existing TUI and focuses it. A dormant branch starts `codex resume <id>` in a
  new workspace; a lock owned outside Herdr is refused instead of reproducing
  Codex's “already has an active writer” error. Native Codex subagents also use
  `forked_from_id`, so their non-user rollout source is explicitly excluded.
  See [Codex slash commands](https://developers.openai.com/codex/cli/slash-commands/).
- **Fork the active Codex session into a right split.** Bind the plugin's
  `fork-right` action and it validates that the invoking pane's reporter,
  terminal title, rollout cwd, foreground Codex PID, and writer lock all name
  the same local session. It then keeps that parent untouched, splits right,
  and starts `codex fork <parent-id>` in the child. The child is focused only
  after its rollout points back to the parent and the two locks have distinct
  owners. Native `/fork` is unchanged; Codex does not expose a slash-command
  dispatch hook for redirecting it into Herdr layout safely.
- **The terminal title wins during Codex's reporter lag.** Immediately after a
  fork or resume, Codex puts the new session ID in its title before Herdr's
  lifecycle hook reports it. A related title ID is therefore treated as the
  current branch, preventing the just-created child from being mislabeled as
  the saved one.
- **Times come from the transcript, because herdr has none.** `pane list`,
  `agent list` and `api snapshot` carry `revision` and `state_change_seq`
  (monotonic counters) and a `focused` flag — no created-at, no
  last-state-change, no last-focused stamp. So *when you last looked at a pane*
  is not recoverable anywhere; the file its agent appends to is the only clock,
  giving last activity (its mtime) and session start (its birth time, landing a
  few seconds after the process actually began).
- **State repair.** A pane herdr never bound to a detected agent keeps
  `agent_status: unknown` forever, even while the agent sits at a prompt. The
  picker feeds that pane's screen to `herdr agent explain` and gets the real
  state back.
- **A preview that reconstructs the session** from its transcript (Claude's
  `~/.claude/projects/<slug>/<id>.jsonl`, the path pi reports, or codex's
  `~/.codex/sessions/<date>/rollout-<stamp>-<id>.jsonl`): wall-clock start and
  last-activity times, the prompts, the replies, shell and edit work folded
  into named runs rather than 114 bash lines, turn/tool/commit/token counts,
  files touched, the last message, then the pane's live screen. Codex writes a
  different schema (`response_item` / `event_msg`), so its rollout supplies the
  times and nothing else — parsing it would buy an empty summary for a
  multi-megabyte read.
- **One list, three routes.** <kbd>enter</kbd> on an agent focuses it; on a
  Codex branch it switches the owning TUI or resumes a dormant session;
  <kbd>enter</kbd> on a folder starts an agent there — which is why folders with
  nothing running are in the list at all. Candidate folders come from launch
  history, then herdr's panes, then [zoxide](https://github.com/ajeetdsouza/zoxide).
- **<kbd>ctrl-a</kbd>** picks which agent kind to start (`claude`, `codex`,
  `pi` — whichever herdr has configured). On an agent row it means "another one
  alongside this", so it launches in that agent's folder.
- **<kbd>ctrl-x</kbd> forgets a folder or archives a dormant saved Codex branch**,
  reloading the list without the dismissed entry. Two of the three folder
  suggestion sources are not ours to prune — zoxide ranks `/tmp` because
  something once `cd`'d there — so folder dismissal is recorded in the state
  file and applied as a filter to all three. A folder with an agent still
  running in it is refused, with a notification saying so; starting an agent
  somewhere later un-forgets it, as does `herdr-agents --unhide [folder]`.
  For a saved Codex branch, ctrl-x calls `codex archive <id>` only after
  confirming that no writer is active. The rollout transcript is preserved and
  restorable with `codex unarchive <id>`; an active branch is refused with a
  notification telling you to open it and use `/archive` in its TUI (or archive
  it in the external owner). See
  [Codex CLI session archival](https://developers.openai.com/codex/cli/reference/#codex-archive-and-codex-unarchive).
- **Searching a folder keeps its agents.** With `--with-nth`, fzf matches a line
  against what it *displays*, so there is no hidden search field to put a cwd
  in and a query naming a folder used to filter out the very agents running in
  it. Each agent row therefore ends in the same path string its header shows —
  last on the line, where a narrow list clips it.
- **Title cards while it launches.** Starting an agent takes a couple of
  seconds; `herdr agent start` runs on a thread and the animation is what the
  main thread does while it waits, so it costs the launch nothing. Four cards
  in rotation — a rotating torus, hyperspace, digital rain, and the agent's
  name in lights. See [The splash](#the-splash).

## Requirements

- [herdr](https://herdr.dev) 0.8 or later, with agents configured — 0.8 is what
  the manifest asks for, since popup pane entrypoints and `plugin pane open`
  arrived there
- [fzf](https://github.com/junegunn/fzf) (0.74+ tested)
- Python 3.10+ (no third-party packages) — as `python3` on the herdr *server's*
  `PATH`, which is what the manifest launches
- optional: the [Codex CLI](https://developers.openai.com/codex/cli/reference/)
  on the herdr server's `PATH` for ctrl-x archival and the `fork-right` action;
  set `CODEX_BIN_PATH` in the server environment when it lives elsewhere
- optional: [zoxide](https://github.com/ajeetdsouza/zoxide), for folder suggestions
- optional for the picker, required by `fork-right`: `lsof`, to map an active
  Codex writer lock to the exact Herdr foreground process; when it is absent,
  active branch ownership and external forking fail closed

## Install

It is a [herdr plugin](https://herdr.dev/docs/plugins/), so herdr can fetch it
itself — no clone, no symlink, nothing on your `PATH`:

```sh
herdr plugin install dleen/herdr-agents
```

That clones the repo into herdr-managed plugin data, shows you the manifest and
the commands it will run, and registers it globally for every session. There is
no build step: the picker is Python with no third-party packages, so
`[[build]]` is absent from the manifest and install is just the checkout.

Check it before wiring up a key:

```sh
herdr plugin list --plugin dleen.herdr-agents
herdr plugin action list --plugin dleen.herdr-agents
```

### Bind it to a key

Add to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+a"
type = "plugin_action"
command = "dleen.herdr-agents.open"
description = "pick an agent"

[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "dleen.herdr-agents.fork-right"
description = "fork Codex right"
```

Then `herdr config check` and `herdr server reload-config` (`prefix+shift+r`).
No restart needed.

The popup and its 90% dimensions live in `herdr-plugin.toml` rather than in the
keybinding, so the key is the same three lines however the picker is sized. A
key binds an *action*, not a pane entrypoint, which is the one hop
`open-picker.sh` exists for: the action asks herdr to open the `picker` pane,
and the manifest says how.

> **Nothing happens on `prefix+a`?** Unlike a `type = "popup"` command, a
> plugin action leaves a record — `herdr plugin log list --plugin
> dleen.herdr-agents` has the exit status and output of every invocation.

### Or as a standalone script

The picker predates the plugin manifest and still runs as a plain script, which
is worth keeping for `--list` from any shell. Clone somewhere permanent and
symlink it onto your `PATH`:

```sh
git clone https://github.com/dleen/herdr-agents.git ~/code/herdr-agents
mkdir -p ~/.local/bin
ln -sf ~/code/herdr-agents/herdr-agents ~/.local/bin/herdr-agents
```

`herdr_splash.py` needs **no** symlink and must not be copied elsewhere: the
script resolves its own `__file__` through the symlink and imports the module
from beside the *real* script, so it can never go stale against the script that
imports it. If it is missing, launching still works — you just get a one-line
"starting…" print instead of an animation.

Bound as a popup command instead of a plugin action, it wants the dimensions in
the keybinding:

```toml
[[keys.command]]
key = "prefix+a"
type = "popup"
command = "herdr-agents"
# Just short of full bleed: the frame still reads as something summoned over
# the session, and the edges of the panes behind it stay visible. 90% is also
# wide enough to keep the side-by-side preview split — herdr-agents stacks the
# preview underneath below 170 columns.
width = "90%"
height = "90%"
```

> **A popup whose command is not on `PATH` fails silently.** If `prefix+a`
> appears to do nothing, check that `~/.local/bin` is on the `PATH` herdr's
> server inherits, and that the symlink resolves. This is the failure the
> plugin install does not have.

### Hacking on it

Link the working tree rather than installing from GitHub, and herdr runs your
edits in place:

```sh
herdr plugin link ~/code/herdr-agents
herdr plugin unlink dleen.herdr-agents
```

Installing over a linked plugin is refused, so unlink before switching back to
`plugin install`.

### Optional: collapse herdr's own agents sidebar

Once this is bound, the sidebar is redundant as a *report* but still useful
at a glance, so collapse it to the status rail rather than to zero width:

```toml
[ui]
sidebar_start_collapsed = true
sidebar_collapsed_mode = "compact"
```

## Usage

| | |
|---|---|
| `herdr-agents` | the picker (needs `HERDR_ENV=1`, i.e. a pane inside herdr) |
| `herdr-agents --list` | print the rows; launches and focuses nothing, works anywhere |
| `herdr-agents --preview <row_key\|§folder>` | render one entry — this is what fzf calls back |
| `herdr-agents --splash [card] [seconds]` | play the launch title cards and exit |

Installed as a plugin there is no `herdr-agents` on your `PATH`, and the
equivalents go through herdr:

| | |
|---|---|
| `herdr plugin action invoke dleen.herdr-agents.open` | the picker, same as the key |
| `herdr plugin pane open --plugin dleen.herdr-agents --entrypoint picker` | the picker, skipping the action |
| `herdr plugin log list --plugin dleen.herdr-agents` | what each invocation did |

The managed checkout is a normal directory, so `--list`, `--preview`, and
`--splash` are still there if you want them:

```sh
root=$(herdr plugin list --plugin dleen.herdr-agents --json | jq -r '.result.plugins[0].plugin_root')
python3 "$root/herdr-agents" --list
```

Inside the picker: <kbd>enter</kbd> focus, switch/resume a Codex branch (or
start, on a folder),
<kbd>ctrl-a</kbd> choose the agent kind, <kbd>ctrl-x</kbd> forget the folder or
archive a dormant saved Codex branch, <kbd>esc</kbd> cancel.

Per-folder launch history lives in
`~/.local/state/herdr-agents/launches.json` — how many agents you have started
where, which kind was last, the folders <kbd>ctrl-x</kbd> has dismissed, and the
splash rotation index. Delete it to start over, or put one folder back with
`herdr-agents --unhide <folder>` (no argument clears every dismissal).

## The splash

```sh
herdr-agents --splash              # every card, three seconds each
herdr-agents --splash donut 6      # one card, six seconds
python3 herdr_splash.py            # same, without herdr in the picture
```

Cards are `donut`, `warp`, `matrix`, `wordmark`. `warp` runs faster the longer
the agent takes; `matrix` uses halfwidth katakana so every glyph is one cell;
`wordmark` spells the agent's name in its own colour.

It steps aside quietly — `HERDR_AGENTS_SPLASH=0`, no tty, `TERM=dumb`, or a
terminal under 34×16 all fall back to the one-line print — and the whole render
loop is wrapped, because a drawing bug must not cost anyone their agent.

## Notes from the build

Things that were measured the hard way and are worth not rediscovering:

- fzf 0.74 picks the wrong branch of a conditional `--preview-window` at any
  width, so `preview_window()` decides the split in absolute cells.
- `--bind=load:down` works where `start:down` silently does not — `start` fires
  before the input is read and the cursor move is lost.
- `herdr agent start` rejects a just-created pane with `agent_pane_busy`; its
  own `--timeout` covers agent readiness, not shell readiness. Hence a retry
  loop backing off from 100ms.
- `herdr agent start` returns on a fixed ~3s interactive-readiness wait, not
  when the agent is up. The agent's own `pane.report_agent_session` lifecycle
  report is the earlier and truthful signal, so the launch races the two and
  ends the card on whichever comes first.
- Agent names must match `[a-z][a-z0-9_-]{0,31}`, which a folder basename like
  `Inbox` is not — `agent_name_base()` slugs it.
- A plugin action is not a terminal. Actions run detached with their output
  going to the plugin log, so fzf cannot live in one; the picker has to be a
  `[[panes]]` entrypoint, and the action is only what a key can name.
- A plugin's `PATH` is the herdr *server's*, not your shell's, and a managed
  install need not put herdr on it at all. Hence `HERDR_BIN_PATH` for every
  callback rather than a bare `herdr`; branch archival similarly uses `codex`
  from that `PATH` unless the server environment supplies `CODEX_BIN_PATH`.
- Codex keeps POSIX writer locks for both sides of an in-TUI `/fork`. Starting
  `codex resume <old-id>` in another pane therefore fails while the original
  TUI lives; `/resume <old-id>` inside that owning TUI is the safe switch.
