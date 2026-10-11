# Visa Voice Agent

A voice front end for the [Visa Research Agent](https://github.com/AadarshSu/visa-research-desk). You describe a trip out
loud; an ElevenLabs agent collects the details, starts live research against the destination's own
government pages, narrates progress while it runs, and answers, including follow-up questions,
only from the finished, cited plan.

## Demo


https://github.com/user-attachments/assets/1d4b62a5-7023-41f1-8317-20a6991a590f


## How it fits together

```
browser (ElevenLabs JS SDK, mic + speaker)
  │  client tools: start_visa_research, check_visa_research
  ▼
server.py (this project, :8200)  ── serves the page, runs research jobs, builds the voice brief
  │  POST /visa-plans/stream (NDJSON: stage events, then plan / refusal / error)
  ▼
Visa Research Agent (:8098, sign-in off)
```

- **Research takes about a minute**, longer than a tool call should block. So `start_visa_research`
  returns at once, and `check_visa_research` long-polls for up to 15 seconds. The agent calls it
  in a loop and mentions each new step.
- **The agent only sees a brief** (`voice_brief` in `server.py`): the decision, who said so, steps,
  exceptions, disagreements and gaps. The full plan with links goes to the screen only. The prompt
  forbids answering from general knowledge; a question the brief can't answer gets "the official
  pages don't say".
- **Conditional answers become questions.** When a plan holds only under a condition ("no more
  than 90 days"), the agent asks the traveller about it instead of reading out a caveat.

## Run

Clone the Visa Research Agent beside this project and set it up as its own
[README](https://github.com/AadarshSu/visa-research-desk#local-setup) describes (a virtual
environment and an `.env` with its OpenAI and search keys):

```bash
git clone https://github.com/AadarshSu/visa-research-desk.git ../visa-research-desk
```

Then set up this project:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then add ELEVENLABS_API_KEY
.venv/bin/python setup_agent.py # creates the agent; run again after editing agent/prompt.md
```

Start both servers, the Visa Research Agent with sign-in off:

```bash
cd ../visa-research-desk && REQUIRE_SIGN_IN=false ANONYMOUS_PLAN_ALLOWANCE=100 .venv/bin/python -m uvicorn visa_research_agent.api.app:create_app --factory --port 8098
```

```bash
.venv/bin/python -m uvicorn server:app --port 8200
```

Open <http://127.0.0.1:8200/> and press **Start talking**.

Each research run is a real plan from the Visa Research Agent and spends its search and model
credits.
