# Setting up Sanjivani

About 20 minutes the first time. Written for Windows; macOS and Linux are the same apart from the installers.

## 1. Install the prerequisites

| What | Why | Get it |
|---|---|---|
| Git (any 2.x) | Agents clone and commit patients | <https://git-scm.com/download/win> (includes Git Bash) |
| GitHub CLI (`gh`) | The Medic opens pull requests (for friends' repos, from a fork in your account); the Undertaker can archive your repos | <https://cli.github.com/>, then run `gh auth login` once |
| Python 3.10+ | Runs `sanjivani.py` (3.9+ works; the weatherly demo needs 3.10+ to show its broken imports) | <https://www.python.org/downloads/>, tick **Add python.exe to PATH** |
| Claude Code (or Codex / OpenCode) | The coding agent behind each Sanjivani agent | `npm install -g @anthropic-ai/claude-code`, then run `claude` once and sign in |
| Docker Desktop (strongly recommended) | The sandbox that runs patients' code with no network, and the built-in Mattermost chat | <https://www.docker.com/products/docker-desktop/> |
| Switch Console | Runs the Switch server and your agents | `switch-console-x64.exe` from <https://github.com/sandbox-quantum/switch/releases/latest> |

Check in a new terminal:

```powershell
git --version
python --version
claude --version
gh auth status
docker info
```

Pull the sandbox images once so the first heartbeat is quick (the toolkit picks one per stack):

```powershell
docker pull python:3.12-slim
docker pull node:20-slim
```

## 2. Publish the toolkit

The template tells each agent to clone the toolkit from GitHub and check out a **release tag**, so the agents never run whatever happens to be on your main branch. Push the repository, then tag it:

```powershell
cd F:\Lazarus\sanjivani
python -m unittest discover -s tests     # all tests should pass
git add .
git commit -m "Sanjivani v0.2.0"
git branch -M main
git push -u origin main                  # origin is github.com/rishikeshvarma/Sanjivani
git tag v0.2.0
git push origin v0.2.0
```

When you change the toolkit later, tag a new version (`v0.2.1`, ...) and change **Toolkit version** in the template form. For the strictest pin, put a full commit hash there instead of a tag.

If your repository is not `https://github.com/rishikeshvarma/sanjivani`, change the **Sanjivani toolkit repository** field when you create from the template (step 5).

## 3. Start a Switch server

1. Open **Switch Console**. The **Setting up Switch** checklist appears in the sidebar.
2. **Add a server → Run a server on this computer.** No Docker needed for the server itself.
3. Keep **Also run Mattermost** on if Docker Desktop is running. You get a local chat app with no workspace or admin rights needed.
4. **Link your messaging account** on the server's Home page (your Mattermost user, or your Discord user if you use Discord).

## 4. Check the agent provider

**Settings → Agent providers**: Claude Code should say **Installed**. Leave **Auto-trust worktree directories** on (Settings → General), or sessions wait forever on a folder-trust prompt.

## 5. Create the Sanjivani room from the template

1. Open **Templates** in Console and import `templates/sanjivani.template.yaml`.
2. The form asks for:
   - **Crew name**: `sanjivani` (agents become `sanjivani-archaeologist`, `sanjivani-medic`, `sanjivani-undertaker`)
   - **Sanjivani toolkit repository**: your GitHub URL from step 2
   - **Toolkit version**: `v0.2.0`, the tag from step 2
   - **Graveyard folder**: where patients live, default `~/sanjivani-graveyard`
   - Advanced: **Largest repository** (50 MB) and **Patients per person per day** (3)
   - **Bridge**: Discord if you set it up, otherwise Mattermost
3. **Create.** Console creates the three agents, the `sanjivani-graveyard` room with you in it, the *Sanjivani protocol* document, and posts the kickoff.
4. Within a minute each agent introduces itself and the Archaeologist creates the *Graveyard Ledger*.

## 6. Smoke test

In the channel:

```
@sanjivani-archaeologist dig up a demo patient
```

It should run `sanjivani.py fixture`, post an autopsy of **demo-weatherly** and ask you for a verdict. Reply `revive`: the Medic confirms the verdict came from you, takes the first heartbeat in Docker (a weak pulse) and starts the revival.

Then check the safety rules once, from a second chat account if you have one (a friend's, or a second Mattermost user):

- the friend says `revive` on a patient: the agents should ask you to decide instead of starting
- the friend gives the Archaeologist a local path or a non-GitHub link: it should refuse
- you say nothing when the Medic asks "push?": nothing should be pushed

Post `!list-documents` to see the protocol and ledger documents.

## Using Discord instead of Mattermost

Follow Switch's Discord guide (<https://docs.switchagents.ai/flintai/switch/deploy/messaging-apps/discord>): create a Discord application with a bot, invite it to your server, and add the Discord bridge in Console. Then choose Discord as the bridge in step 5. Invite friends to the channel so they can bring their own dead projects to the Archaeologist.

## Troubleshooting

- **An agent never answers**: it only wakes when mentioned. Use its full name, e.g. `@sanjivani-medic`. Check its session in Console.
- **`python` not found inside an agent**: reinstall Python with *Add to PATH*, then restart Switch Console.
- **Vitals say `node_modules missing`**: expected for a Node patient without `--install`; the Medic installs with `--install` in Docker, or `npm install --ignore-scripts` on the host.
- **"Docker is not running"**: start Docker Desktop. The Medic will otherwise ask you before running a patient's tests directly on your computer.
- **`refused: ... GitHub's API limit was hit`**: the size check uses GitHub's public API (60 requests an hour). Wait, or as the owner add `--skip-size-check` to your own request.
- **The Medic waits for approval**: that is auto mode asking about a risky command. Approve it in the thread or in Console.
- More: <https://docs.switchagents.ai/flintai/switch/resources/troubleshooting>
