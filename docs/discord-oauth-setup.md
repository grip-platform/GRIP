# GRIP Discord Bot — Guild OAuth Setup

## Why this is needed

The GRIP bot's `/evolve-broadcast` command posts updates to Discord by
calling the Discord API with a target channel ID. If the bot application
has not been added to the guild (server) that owns that channel, Discord
returns a 404 error. The 404 does not raise an exception that stops the
broadcast — the Discord post silently fails while the Slack post
succeeds. The target guild is `1470132601518952621`. Until the bot is
authorised as a member of that guild, every Discord broadcast will keep
returning 404 with no visible error to the operator.

## Operator action steps

1. Open the Discord Developer Portal at
   `https://discord.com/developers/applications` and sign in with the
   account that owns the GRIP bot application.

2. Select the GRIP application from your application list. Navigate to
   **General Information** and copy the value labelled **Application ID**.
   This is your `APP_ID`.

3. Construct the OAuth invite URL by replacing `<APP_ID>` with the value
   you copied:

   ```
   https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=2048
   ```

   The `permissions=2048` value grants the **Send Messages** permission
   only. If the GRIP bot needs to embed content, attach files, or perform
   other actions in Discord, add the corresponding permission integers to
   `2048`. The full list of permission values is at
   `https://discord.com/developers/docs/topics/permissions`.

4. Open the constructed URL in a browser. Discord will present an
   authorisation screen. From the **Add to Server** dropdown, select the
   guild with ID `1470132601518952621`. Select the correct server, then
   select **Authorise**. Complete any CAPTCHA prompt if shown.

5. After authorisation, navigate to the Discord server and locate the
   channel where the GRIP bot should post. Enable **Developer Mode** in
   Discord if it is not already on: go to **User Settings** > **Advanced**
   > turn on **Developer Mode**. Right-click the target channel and
   select **Copy Channel ID**. This is the value you will use for
   `GRIP_DISCORD_CHANNEL`.

6. Update the environment variable in the shell session running GRIP:

   ```bash
   export GRIP_DISCORD_CHANNEL=<channel_id>
   ```

   To persist this across shell sessions, add the same line to your
   shell profile (for example `~/.zshrc` or `~/.bashrc`), then reload
   the profile:

   ```bash
   source ~/.zshrc
   ```

## Verification

Run `/evolve-broadcast` from your active GRIP session. Previously, the
Discord post returned `404`. After completing the steps above, the
Discord post should return `200` and the message should appear in the
target channel. If the Slack post was already working, it will continue
to work — no change is needed there.

## Falsifier

This document is wrong if you complete all steps above and
`/evolve-broadcast` still returns `404` on the Discord path. Possible
causes to investigate:

- **Insufficient permissions**: The `permissions=2048` value only grants
  Send Messages. If the target channel has permission overrides that
  restrict bots, the bot may be in the guild but still blocked from
  posting. Check the channel's permission settings and add the bot role
  explicitly if needed.

- **Wrong guild**: Confirm that the guild you selected during the OAuth
  authorisation step is the guild that owns the channel you copied the
  ID from. A channel ID from a different guild will always return `404`
  regardless of bot membership.

- **Environment variable not active in the GRIP process**: If GRIP is
  running in a separate shell session or as a background process,
  `export` in your current terminal does not propagate to it. Restart
  the GRIP process after updating the variable, or set the variable in
  the process's launch configuration directly.

- **Bot was removed after authorisation**: A guild administrator can
  kick a bot after the OAuth invite. If `/evolve-broadcast` worked
  briefly and then returned to `404`, check the guild's member list to
  confirm the bot is still present.

Until this is done, `/evolve-broadcast` will keep posting to Slack
successfully and silently failing on Discord — the Slack path is the
working fallback.
