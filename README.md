# herdr-agents

An fzf picker for every agent pane [herdr](https://herdr.dev) knows about —
bound to a key, it opens as a popup, lists your agents worst-first, previews
what each one has actually been doing, and starts new ones in folders that
have nothing running.

```
agent> ▏
  ~/code/service-a  (2)
    ! 3  needs input   12s  claude  waiting on approval to force-push
    * 1  working       2s   pi      refactoring the retry loop
  ~/code/notes  (1)
    o 4  idle          8m   codex   —
  ~/code/infra  + pi
```

Herdr's own sidebar agents panel is a ~30-column rail, and it lists only the
panes whose agent state herdr managed to detect — a pane whose agent process it
never classified is missing entirely. This reads `herdr pane list` instead and
resolves state itself, so those panes show up, in a popup with room for a real
preview.

## What it does

- **One row per agent pane**, grouped by working directory (full `cwd`, so two
  worktrees of the same repo stay distinct) and sorted worst-first: blocked →
  working → idle/done → unknown. A folder with something waiting on a human
  floats to the top of the list.
- **State repair.** A pane herdr never bound to a detected agent keeps
  `agent_status: unknown` forever, even while the agent sits at a prompt. The
  picker feeds that pane's screen to `herdr agent explain` and gets the real
  state back.
- **A preview that reconstructs the session** from its transcript (Claude's
  `~/.claude/projects/<slug>/<id>.jsonl`, or the path pi reports): the prompts,
  the replies, shell and edit work folded into named runs rather than 114 bash
  lines, turn/tool/commit/token counts, files touched, the last message, then
  the pane's live screen.
- **One list, two verbs.** <kbd>enter</kbd> on an agent focuses it;
  <kbd>enter</kbd> on a folder starts an agent there — which is why folders with
  nothing running are in the list at all. Candidate folders come from launch
  history, then herdr's panes, then [zoxide](https://github.com/ajeetdsouza/zoxide).
- **<kbd>ctrl-a</kbd>** picks which agent kind to start (`claude`, `codex`,
  `pi` — whichever herdr has configured). On an agent row it means "another one
  alongside this", so it launches in that agent's folder.
- **Title cards while it launches.** Starting an agent takes a couple of
  seconds; `herdr agent start` runs on a thread and the animation is what the
  main thread does while it waits, so it costs the launch nothing. Four cards
  in rotation — a rotating torus, hyperspace, digital rain, and the agent's
  name in lights. See [The splash](#the-splash).

## Requirements

- [herdr](https://herdr.dev) 0.8 or later, with agents configured
- [fzf](https://github.com/junegunn/fzf) (0.74+ tested)
- Python 3.10+ (no third-party packages)
- optional: [zoxide](https://github.com/ajeetdsouza/zoxide), for folder suggestions

## Install

Clone somewhere permanent and symlink the script onto your `PATH`:

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

Check it before wiring up a key:

```sh
herdr-agents --list      # print the rows; works outside a herdr session too
```

### Bind it to a key

Add to `~/.config/herdr/config.toml`:

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

Then `herdr config check` and `herdr server reload-config` (`prefix+shift+r`).
No restart needed.

> **A popup whose command is not on `PATH` fails silently.** If `prefix+a`
> appears to do nothing, check that `~/.local/bin` is on the `PATH` herdr's
> server inherits, and that the symlink resolves.

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
| `herdr-agents --preview <pane_id\|§folder>` | render one entry — this is what fzf calls back |
| `herdr-agents --splash [card] [seconds]` | play the launch title cards and exit |

Inside the picker: <kbd>enter</kbd> focus (or start, on a folder),
<kbd>ctrl-a</kbd> choose the agent kind, <kbd>esc</kbd> cancel.

Per-folder launch history lives in
`~/.local/state/herdr-agents/launches.json` — how many agents you have started
where, which kind was last, and the splash rotation index. Delete it to start
over.

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
