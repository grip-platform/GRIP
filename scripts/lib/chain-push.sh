# chain-push.sh -- push the public decision-chain clone, as a GitHub App when one
# is configured, so the repository's public activity names the App, not a person.
#
#   chain_push <clone> <python>
#
# With an App config (GRIP_CHAIN_APP_CONFIG, default ~/.config/grip/chain-app/app.json):
#   - mint a one-hour installation token through lib/github_app_token.py;
#   - push with that token alone: every credential helper is switched off,
#     prompts are disabled, and every earlier http extraheader (the clone's,
#     the user's or the system's) is reset before the token's header is added,
#     so no other Authorization header rides along and a failed or rejected
#     token can never fall back to a personal credential;
#   - refuse (return 1, nothing pushed) when no token can be minted or when the
#     clone's origin is not an https://github.com/ URL, because only those URLs
#     receive the token's header.
# The token travels in GIT_CONFIG_* environment variables, never in argv.
#
# With no App config it refuses by default, so a mistyped path, a different $HOME
# or an App not yet set up can never quietly publish as a person. Only an explicit
# GRIP_CHAIN_REQUIRE_APP=0 pushes with the stored credential, and says so on stderr.
#
# Sourced by bin/grip-chain-sync, which stops on a non-zero return.
# tests/test_chain_push.py and tests/test_grip_chain_sync_push.py drive it with a fake git.

chain_push() {
  local clone="$1" py="$2"
  local cfg="${GRIP_CHAIN_APP_CONFIG:-$HOME/.config/grip/chain-app/app.json}"
  if [ ! -f "$cfg" ]; then
    if [ "${GRIP_CHAIN_REQUIRE_APP:-1}" != "0" ]; then
      echo "[grip-chain-sync] no App config at $cfg -- NOT pushing (the App is required; GRIP_CHAIN_REQUIRE_APP=0 would push with the stored credential)" >&2
      return 1
    fi
    echo "[grip-chain-sync] no App config at $cfg and GRIP_CHAIN_REQUIRE_APP=0 -- pushing with the stored credential" >&2
    git -C "$clone" push -q origin main
    return
  fi
  # git pushes to EVERY push URL of origin, so every one must receive the token's
  # header; an SSH one would authenticate with the operator's own key instead.
  local urls url
  urls="$(git -C "$clone" remote get-url --push --all origin)" || return 1
  if [ -z "$urls" ]; then
    echo "[grip-chain-sync] origin has no push URL -- NOT pushing with the App" >&2
    return 1
  fi
  while IFS= read -r url; do
    if [[ "$url" != https://github.com/* ]]; then
      echo "[grip-chain-sync] a push URL of origin is not an https://github.com/ URL -- NOT pushing with the App" >&2
      return 1
    fi
  done <<< "$urls"
  # The token module sits beside this file in the same checkout, wherever
  # CLAUDE_DIR points: scripts/lib/../.. is the checkout root that holds lib/.
  local root
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  local token
  if ! token="$(PYTHONPATH="$root:$root/lib" "$py" -m lib.github_app_token "$cfg")" || [ -z "$token" ]; then
    echo "[grip-chain-sync] GitHub App token unavailable -- NOT pushing (no fallback to a personal credential;" \
         "GRIP_CHAIN_REQUIRE_APP=0 applies only when no App config exists, so move $cfg aside to push with the stored credential)" >&2
    return 1
  fi
  local auth
  auth="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
  # An empty extraheader resets the list git has read so far (git config docs),
  # so entries 0 and 1 drop any earlier header and entry 2 is the only one sent.
  # An empty GIT_ASKPASS stops git asking any askpass program (git consults it
  # before GIT_TERMINAL_PROMPT, and before core.askPass and SSH_ASKPASS), so a
  # rejected token cannot be answered with a person's credential.
  GIT_TERMINAL_PROMPT=0 GIT_ASKPASS= SSH_ASKPASS= GIT_CONFIG_COUNT=3 \
    GIT_CONFIG_KEY_0="http.extraheader" GIT_CONFIG_VALUE_0="" \
    GIT_CONFIG_KEY_1="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_1="" \
    GIT_CONFIG_KEY_2="http.https://github.com/.extraheader" \
    GIT_CONFIG_VALUE_2="AUTHORIZATION: basic $auth" \
    git -C "$clone" -c credential.helper= push -q origin main
}
