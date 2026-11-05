# Demo script (target: 3 minutes)

Judging is 30% use of Switch, 25% does it run, 25% creativity and impact, 20% presentation. The video has to make **Switch's role** obvious and show **one full revival and one burial**.

## Before recording

- Pick two real dead repos of your own: one small and revivable (the patient), one you are happy to bury. Have weatherly ready as a backup (`@sanjivani-archaeologist dig up a demo patient`).
- Start Docker Desktop and pull the images (see SETUP.md) so the first heartbeat does not wait on a download.
- Do a full dry run first. Then reset: delete `~/sanjivani-graveyard` (it holds the patients and the `.state` notes) and clear the ledger.
- Put Discord/Mattermost on the left, Switch Console on the right. Zoom the chat font to 125%.
- If revival takes long, record in pieces and cut the waiting.

## Shots

**0:00 to 0:20, the hook.** Show your GitHub repo list and scroll through the dead ones.
> "Every developer has a graveyard. These are mine. So I built a room where AI teammates and I decide what to do with them: revive, scope down, or bury."

**0:20 to 0:40, the setup is Switch.** Show `sanjivani.template.yaml` in Console's Templates page, then the room with three agents.
> "This is one Switch template: three agents, Archaeologist, Medic and Undertaker, a room on Discord, and a shared protocol document. My friends are in this channel too."

**0:40 to 1:20, the autopsy.** `@sanjivani-archaeologist dig up <repo>`. Show the autopsy arrive: last words, causes of death, the removed APIs.
> "It finds out why the project died, down to the exact line that broke when Python moved on, just by reading it. It never runs a stranger's code."

**1:20 to 2:05, revival.** Reply `revive`. Show the hand-off to `@sanjivani-medic`, the "ran in Docker with no network" line on the first heartbeat, the plan, a vitals line going from weak to strong, the certificate, then the Medic asking "push?" and you replying yes. Flash the PR on GitHub.
> "The patient's code only runs in a sandbox, and nothing is pushed until I say so. Agents advise, I decide."

Optional 10 seconds that judges remember: have a friend's account say `revive` on another patient and show the agent asking you instead.

**2:05 to 2:40, the funeral.** Bring the second repo and reply `bury`. The Undertaker asks two questions; answer honestly. Show the tombstone in chat and the organ bank folder.
> "Even a dead project leaves something reusable behind."

**2:40 to 3:00, the ledger.** `!list-documents`, then ask "what's in the graveyard?" and show the ledger.
> "Sanjivani: built on Switch, open source, link below. Go visit your own graveyard."

## Submission checklist

- [ ] Public GitHub repo with this README, the template and `sanjivani.py`
- [ ] Demo link in the README
- [ ] README shows clearly how Switch is used (the "Why it needs Switch" table)
- [ ] `python -m unittest discover -s tests` passes
- [ ] Submitted on the hackathon page before **Nov 7, 11:59 PM IST**, Track 2
- [ ] Optional: blog post (Best Blog side track) and a #HowISwitch post with a clip of the funeral
