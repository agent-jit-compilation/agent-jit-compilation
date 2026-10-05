# Benchmark tasks

The paper evaluates **37 tasks** (Section 4.1 and Appendix D, Table 10): 27 REAL tasks and 10 WebArena tasks. JIT-Planner uses all 37. JIT-Scheduler uses the 27 REAL tasks.

The catalog is [benchmarks/tasks.json](../benchmarks/tasks.json). It includes exact executable instructions, task aliases, component membership, cardinality, baseline length, added-task flags, and verifier paths. The tables below show those instructions; the paper abbreviates some entries.

```sh
uv run agent-jit tasks --component planner
uv run agent-jit tasks --component scheduler
uv run agent-jit tasks gomail-custom-8
```

The last command also resolves the starting URL and task definition. Both a paper ID and its implementation alias can be used in a YAML `tasks` list. An em dash in the alias column means the IDs are identical.

## Scheduler task set

For each REAL application, the paper selects three existing benchmark tasks and adds six, for **18 added tasks** in total. Each application has three tasks whose measured oracle strategy is Serial, three Parallel, and three Hedge. The oracle is determined with GPT-4.1 by the lowest mean latency over three runs of each strategy; it is a reference label rather than a hardcoded instruction to JIT-Scheduler. All these tasks are also in the planner task set.

### Dashdish

Starting URL: [https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/](https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/).

Task definitions: [experiments/tasks/dashdish.yaml](../experiments/tasks/dashdish.yaml). Inspect any task with `agent-jit tasks <id>` for its starting URL and verifier.

| Paper task ID | Implementation alias | Instruction | Oracle | Added |
| --- | --- | --- | --- | --- |
| `dashdish-custom-1` | — | How much is a matcha croissant and a regular iced matcha latte from stonemill matcha? What about a ceremonial matcha latte and a mochi set? | Serial | Yes |
| `dashdish-custom-2` | — | For each vendor under the 'Costume party ready' category, visit the store page and get the first three reviews. | Parallel | Yes |
| `dashdish-custom-3` | — | Buy a taco from any store and make sure you place the order. | Hedge | Yes |
| `v2/dashdish-10` | `dashdish-custom-4` | Place an order for any type of sub-sandwich, keep the total under $30 | Hedge | No |
| `v1/dashdish-1` | `dashdish-custom-5` | What are the first three restaurants listed on the homepage? | Serial | No |
| `dashdish-custom-6` | — | Visit the first five restaurants in "Best near you" and report how many of them have under 20 reviews? | Parallel | Yes |
| `dashdish-custom-7` | — | What's the most expensive item from Petco? | Hedge | Yes |
| `v1/dashdish-3` | `dashdish-custom-8` | How many restaurants in the "Light & fresh" category offer delivery? | Serial | No |
| `dashdish-custom-9` | — | Tell me which of Wingstop, Man vs. Fries, McDonald's, Popeyes, and Ike's has the cheapest fries item and its price. | Parallel | Yes |

### Gomail

Starting URL: [https://real-gomail-1glk4rtaf-real-sites.vercel.app/](https://real-gomail-1glk4rtaf-real-sites.vercel.app/).

Task definitions: [experiments/tasks/gomail.yaml](../experiments/tasks/gomail.yaml). Inspect any task with `agent-jit tasks <id>` for its starting URL and verifier.

| Paper task ID | Implementation alias | Instruction | Oracle | Added |
| --- | --- | --- | --- | --- |
| `v1/gomail-1` | `gomail-custom-1` | How many unread emails are in the Inbox? | Serial | No |
| `gomail-custom-2` | — | Forward the first two emails in my inbox to me@example.com, each with a short note "Forwarded from my inbox", and then delete the two original emails. | Hedge | Yes |
| `v1/gomail-3` | `gomail-custom-3` | Compose a new email to jonathan.smith@example.com with the subject "Meeting Notes" and body "Please find the meeting notes attached." | Serial | No |
| `v2/gomail-14` | `gomail-custom-4` | Clear all my emails that are visible please! | Hedge | No |
| `gomail-custom-5` | — | Write an email to each of 'alice', 'bob', 'charlie', 'david'@example.com with personalized greetings and a simple Merry Christmas message. | Parallel | Yes |
| `gomail-custom-6` | — | Reply to each of the first four emails in my inbox with "unsubscribe" | Parallel | Yes |
| `gomail-custom-7` | — | Delete all emails from 'Alerts', 'Newsletter', or 'Notifications'. | Hedge | Yes |
| `gomail-custom-8` | — | Create three new labels: 'Work', 'Personal', 'Urgent'. | Parallel | Yes |
| `gomail-custom-9` | — | Draft a new email and send it to any four different people. | Serial | Yes |

### Omnizon

Starting URL: [https://amazon-omnizon-z1-rea-lr9f-agi7-xw2-pq6s-git-b7a192-real-sites.vercel.app/](https://amazon-omnizon-z1-rea-lr9f-agi7-xw2-pq6s-git-b7a192-real-sites.vercel.app/).

Task definitions: [experiments/tasks/omnizon.yaml](../experiments/tasks/omnizon.yaml). Inspect any task with `agent-jit tasks <id>` for its starting URL and verifier.

| Paper task ID | Implementation alias | Instruction | Oracle | Added |
| --- | --- | --- | --- | --- |
| `omnizon-custom-1` | — | Find the most expensive order on Returns & Orders page and cancel it. | Serial | Yes |
| `omnizon-custom-2` | — | Go to Returns & Orders page and write a product review for each item in the first three orders. | Parallel | Yes |
| `omnizon-custom-3` | — | Cancel the three orders placed in July on the Returns & Orders page. | Serial | Yes |
| `omnizon-custom-4` | — | How many orders have not yet shipped? How many cancelled? | Serial | Yes |
| `v1/omnizon-10` | `omnizon-custom-5` | Click on "buy now" on any product, increase its quantity to the maximum allowed, update the delivery date to the last available, and place the order. | Hedge | No |
| `omnizon-custom-6` | — | Which of the following coffee makers has touchscreen: Gevi 10, Gevi 12, Simply Good Coffee, and BELLA Single Serve? Make sure you visit the product pages of each of them. | Parallel | Yes |
| `omnizon-custom-7` | — | Visit "Gift", "Toy", "Gaming", and "Cosmetic" categories on the menu bar. For each, click on the first item and report the product color. | Parallel | Yes |
| `v1/omnizon-8` | `omnizon-custom-8` | Search for "Automatic Espresso Machine," click on the cheapest one, change the quantity to 5, use "buy now" to purchase them and complete the checkout. | Hedge | No |
| `v2/omnizon-9` | `omnizon-custom-9` | I want to create a gaming collection - buy any gaming device under $100. | Hedge | No |

## Planner-only tasks

The paper randomly selects five GitLab and five Reddit tasks from WebArena. These are evaluated with the planner only and have no oracle scheduling label. Use [WebArena setup](WEBARENA.md) to start their local applications.

### GitLab

Starting URL: [http://localhost:8023](http://localhost:8023).

Task definitions: [experiments/tasks/webarena-gitlab.yaml](../experiments/tasks/webarena-gitlab.yaml). Inspect any task with `agent-jit tasks <id>` for its starting URL and verifier.

| Paper task ID | Implementation alias | Instruction | Oracle | Added |
| --- | --- | --- | --- | --- |
| `webarena-747` | — | Start a private project awesome_web_agents with blank template and add Abishek, Vinta as members | — | No |
| `webarena-308` | — | Tell me who has made the most contributions, in terms of number of commits, to the primer/design project | — | No |
| `webarena-414` | — | Make the LICENSE of byteblaze/dotfiles to MIT license | — | No |
| `webarena-133` | — | How many commits did Eric make to a11yproject on 3/2? | — | No |
| `webarena-419` | — | Set my gitlab status as Enjoying life | — | No |

### Reddit

Starting URL: [http://localhost:9999](http://localhost:9999).

Task definitions: [experiments/tasks/webarena-reddit.yaml](../experiments/tasks/webarena-reddit.yaml). Inspect any task with `agent-jit tasks <id>` for its starting URL and verifier.

| Paper task ID | Implementation alias | Instruction | Oracle | Added |
| --- | --- | --- | --- | --- |
| `webarena-29` | — | Tell me the count of comments that have received more downvotes than upvotes for the user who made the latest post on the DIY forum | — | No |
| `webarena-727` | — | Dislike all submissions created by PatientBuilder499 in subreddit videos | — | No |
| `webarena-580` | — | Create a new forum named sci_fi, with a description of 'A wild place for sci-fi enthusiasts', and include ['New', 'Classic', 'Movies', 'Post my novel', 'Random'] in the sidebar | — | No |
| `webarena-399` | — | Change my reddit bio to "I am a robot" | — | No |
| `webarena-404` | — | Upvote the newest post in books subreddit | — | No |

## Selecting tasks for a run

Copy the appropriate example configuration and edit its task list:

```yaml
tasks:
  - gomail-custom-8
  - v1/gomail-1
trials: 3
```

Use a separate configuration for WebArena, with `evaluator: webarena_native` and its environment settings. The scheduler adapter accepts REAL tasks only. Setup and online execution commands are in the [README](../README.md).
