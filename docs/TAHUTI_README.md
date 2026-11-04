# Tahuti & Sensei — how to use them

**Tahuti** is the voice you talk to. **Sensei** is the AI that works in your terminal. The avatar on your screen shows what Tahuti is doing and gives you cards to read and click.

## 1. Talk to it

- Hold the trigger (**L + R** on the controller, or **F13** on a keyboard) and speak. Let go when you are done.
- The avatar appears while you talk, stays while it works, and goes away when it has finished speaking.
- Ask anything: "What is two plus two?", "Open YouTube", "Give me the newest AI tools updates on a web page."

## 2. Slash commands by voice

Say **"slash"** and then the command name. Every Sensei slash command works this way, and nothing is sent to an AI model — commands run instantly.

| Say | What happens |
|---|---|
| slash compact | Saves and summarises the chat, then restarts Sensei with a shorter history |
| slash plan | Switches Sensei to plan mode (also: slash review) |
| slash mode | Opens a menu: current mode, Auto, Connected, Local, Plan, Review |
| slash models | Opens the model picker so you can choose which model answers |
| slash help  (or: how do I use this) | Opens the command guide — every command and the words to say |
| slash sensei help | Opens Sensei's own help pages |
| slash commands | Same command guide |

If a command has ready-made choices, saying just its name opens a menu of buttons. Menus exist for: `slash clear`, `slash few_shot`, `slash git`, `slash hints`, `slash hooks`, `slash load`, `slash mode`, `slash mouse`, `slash privacy`, `slash save`, `slash tf`, `slash tts`.

Some commands are protected and ask you to approve first: `clear`, `exit`, `forget`, `go`, `quit`, `restart`, `unload`, `x`, and `git commit`, `hooks disable`.

If you say a command that does not exist, Tahuti tells you and suggests the closest ones.

## 3. Using the cards

| Key | Does |
|---|---|
| ◀  (Left) | Back / previous choice / scroll up |
| ▶  (Right) | Next / next choice / scroll down |
| ⏎  (Enter) | Select |
| Esc | Close or cancel |
| 1 – 6 | Pick that number in a menu |

You can also click the buttons. When a card has more text than fits, the bottom line shows `▼ 1-10/27 ◀ ▶ more` — keep pressing ▶ to read on, or use the mouse wheel.

## 4. Controller buttons

| Button | Does |
|---|---|
| A | Click |
| B | Bksp |
| X | Del |
| Y | Super |
| LB | Shift |
| RB | R·Clk |
| LT | Ctrl |
| RT | Talk |
| ⧉ | Kbd |
| ☰ | Tab |
| LS | Esc |
| RS | Enter |

## 5. Every slash command (A–Z by group)

"…" means the command needs more words after it, for example `slash remember …` then what to remember.

### General

| Say | What it does |
|---|---|
| slash approved | approved command list |
| slash apps | app tools |
| slash autotips | automatic tips |
| slash cache | cache status |
| slash chats | browse saved chats |
| slash clean | open Sensei Clean dashboard |
| slash clear | full session reset + engine restart (same as new) |
| slash clear approved | clear approved commands |
| slash clear cache | clear answer cache |
| slash clear chats | clear saved chats |
| slash clear history | clear input history |
| slash commands | print the full alphabetical command list |
| slash compact | save + summarize + restart fresh |
| slash compress | alias for compact |
| slash controls | terminal shortcuts and copy/paste rules |
| slash doctor | system health |
| slash help | quick reference |
| slash hub | open full command menu |
| slash keys | API key setup |
| slash kick | force-restart the engine if it is stuck |
| slash load session | load saved chat |
| slash load summary | load compact context |
| slash log | show log path |
| slash master update | update Master AI safely |
| slash new | full session reset + engine restart |
| slash perms | permissions status |
| slash preview | open latest preview |
| slash project | current project |
| slash projects | project picker |
| slash refresh | soft reload |
| slash restart | restart Sensei |
| slash save session | save current chat |
| slash slideshow | open slideshow |
| slash tips | show practical command tips |
| slash tour | open walkthrough |
| slash transcript | save transcript |
| slash tutorial | replay walkthrough |
| slash update | update Master AI safely |

### Settings & modes

| Say | What it does |
|---|---|
| slash accessibility | input settings |
| slash few shot | few-shot example injection status |
| slash few shot off | disable harvest few-shot injection |
| slash few shot on | enable harvest few-shot injection |
| slash few shot status | show few-shot toggle state |
| slash hints | hint status |
| slash hints off | disable contextual hints |
| slash hints on | enable contextual hints |
| slash mode | show current execution mode |
| slash mode auto | run non-destructive work |
| slash mode connected | cloud-first when keys exist |
| slash mode local | force local/offline routing |
| slash mode plan | draft plans only |
| slash mode review | confirm each action |
| slash model | pick active model |
| slash model auto | return model routing to automatic |
| slash model local | select Sensei primary |
| slash model qwen2.5vl:3b | select the single local VLM (language + vision) |
| slash model stats | show individual model usage |
| slash mouse local | terminal copy mode |
| slash mouse remote | phone scrolling mode |
| slash mouse status | show mouse mode |
| slash privacy approve send | approve one cloud send when turn is private |
| slash privacy status | show privacy state for this turn |
| slash reasoning … | AI reasoning depth — /reasoning low\|medium\|high\|max\|off\|auto |
| slash reasoning status | show the current reasoning depth and which lanes honor it |
| slash tts | voice status |
| slash tts off | disable voice replies |
| slash tts on | enable voice replies |

### Tools

| Say | What it does |
|---|---|
| slash agent … | plan/execute/critique task loop |
| slash agents | show registered subagents |
| slash agents inspect … | type a subagent name after this |
| slash agents list | show registered subagents |
| slash agents run … | type a subagent name + task after this |
| slash dl | download helper |
| slash forget … | type keyword after the colon |
| slash gdrive | Google Drive helper |
| slash git | git command help |
| slash git commit … | commit workflow |
| slash git diff | show repo diff |
| slash git log | show git log |
| slash git status | show repo status |
| slash hooks | show registered hooks (built-in + user) |
| slash hooks disable … | type a hook id to disable it |
| slash hooks enable … | type a hook id to enable it |
| slash hooks list | show registered hooks |
| slash hooks reload | reload user hooks from ~/.master_ai_hooks.json |
| slash image latest … | show latest generated image |
| slash image status … | fetch/show an image job result |
| slash image … | type an image prompt after the colon |
| slash max … | max reasoning with mandatory self-critique |
| slash memory … | show / edit the facts Sensei remembers |
| slash mesh … | LAN mesh agents — ask / status |
| slash read … | read a file into the conversation |
| slash reason deep … | DeepSeek-R1, else local deep loop (default) |
| slash reason fast … | local fast reasoning (planner→solver→finalizer) |
| slash reason max … | local max loop (mandatory second-critic pass) |
| slash reason standard … | cloud DeepSeek-R1, else local standard loop |
| slash reason … | deep reasoning lane (DeepSeek-R1 or local loop) |
| slash remember … | type your text after the colon |
| slash router … | show what the router picks for the next ask, and why |
| slash router stats | routing stats per model — hits, misses, latency |
| slash search … | web search route |
| slash stats | observability rollup: routes, models, blocked, harvest |
| slash task | task command help |
| slash task add … | type task text after this |
| slash task clear | clear tasks |
| slash task done … | mark a task complete |
| slash tasks | show active tasks |
| slash tf fetch … | fetch one URL via TinyFish — clean markdown |
| slash tf search … | web search via TinyFish |
| slash tf status | TinyFish lane status — key, quotas |
| slash tinyfish | TinyFish web tools |

### Plan mode

| Say | What it does |
|---|---|
| slash cancel | cancel pending plan |
| slash e | edit thread label |
| slash go | accept pending plan |
| slash only | make Sensei the only tmux pane |

### Scrolling

| Say | What it does |
|---|---|
| slash bottom | jump to latest chat |
| slash down | scroll chat down |
| slash last | show last reply |
| slash top | jump to oldest chat |
| slash up | scroll chat up |

### Exit

| Say | What it does |
|---|---|
| slash x | exit / stop |

## 6. Models

- Say **slash models** to pick a provider and model. Sensei and Tahuti's voice use the same choice.
- On **Auto**, Sensei's router chooses for typed work and Tahuti answers with Meta Muse.
- If the model you picked ever fails, Tahuti says so out loud and falls back to Muse.
- Meta Muse (Muse Spark / Wonder) is a provider in the picker.

## 7. Where things are

| What | Where |
|---|---|
| Your chats | `~/.master_ai_chats` — say **slash chats** to browse them |
| Sensei's settings | `~/.master_ai_settings` and `~/.master_ai_active_model` |
| Tahuti's goal queue and history | `~/.tahuti/goals` (finished goals in `done/`) |
| Card log (if a card misbehaves) | `~/.tahuti/slash_card.log` |
| This README | `~/.tahuti/README.html` (regenerate: `python3 ~/ai-controller/scripts/make_tahuti_readme.py`) |

## 8. The reminder

About every two hours the avatar asks if you want to learn more. Choose **Open README**, **Later**, or **Turn off**. Say **slash reminders on** to bring it back, **slash reminders off** to silence it, or **slash readme** to open this page any time.

## 9. If something is wrong

- No sound: check the HDMI port is the one your speakers use. Tahuti falls back to a built-in voice if the main voice is out of credits.
- A card is stuck: press **Esc**. If that does not help, say **slash commands** to get a fresh one.
- Services: `systemctl --user status ai-controller-goal-runner voice-bridge ptt-pynput`.
