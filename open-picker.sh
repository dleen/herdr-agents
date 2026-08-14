#!/bin/sh
# The `open` action: raise the picker popup.
#
# Actions run detached with their output going to the plugin command log, so an
# action cannot *be* the picker -- fzf needs a terminal. It asks herdr for the
# `picker` pane entrypoint instead, which is where the popup placement and its
# dimensions are declared. Placement is deliberately not repeated here: a
# --placement flag would override the manifest and split the answer across two
# files.
set -eu

exec "${HERDR_BIN_PATH:-herdr}" plugin pane open \
	--plugin "${HERDR_PLUGIN_ID:-dleen.herdr-agents}" \
	--entrypoint picker
