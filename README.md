# Pexip Whiteglove Scheduler

A polished Flask-based scheduler for Pexip whiteglove VMR and endpoint orchestration.

## What it does

- Schedules meetings using aliases in the format `doc<16 alphanumeric>`
- Pulls registered endpoints from the Pexip Management Status API on `cklab-pexmgr.ck-collab-engtest.com`
- Uses the Pexip Management Command API on `cklab-edges.ck-collab-engtest.com` to dial and disconnect endpoints
- Lets you select endpoints to be dialed automatically when the meeting starts
- Automatically starts meetings by dialing selected endpoints into the target alias
- Automatically ends meetings by disconnecting matching live participants at end time
- Shows a day timeline with status colors:
  - Grey = scheduled
  - Yellow = about to start
  - Green = started
  - Blue = ended
- Lets you extend live meetings from the timeline or queue

## Lab host split used in this build

This package is pre-wired to your requested split:

- **Registration status API host:** `cklab-pexmgr.ck-collab-engtest.com`
- **Command API host for dial/disconnect:** `cklab-edges.ck-collab-engtest.com`

The backend exposes both values in `/api/config` and `/api/health` so you can confirm the running service is pointed correctly.

## Important deployment note

For `doc<16>` aliases to work automatically, your Pexip platform must already know how to route or dynamically create those aliases. In practice that usually means one of these is in place:

1. a local policy or external policy that accepts `^doc[a-zA-Z0-9]{16}$`
2. a service configuration pattern that maps those aliases to a VMR workflow
3. an existing provisioned VMR inventory that already contains the alias

This app assumes the alias is already routable in Pexip when the first outbound dial is placed.

## API assumptions used in this build

- Registered endpoints are read from Management Status API on the management node
- Endpoint dial-out uses Management Command API `participant/dial` on the edge
- Meeting end uses Management Command API `participant/disconnect` on the edge
- Live participant visibility is still read from the status API on the management node so the scheduler can identify who is still attached to a scheduled alias before disconnecting them

## Quick start

```bash
cd pexip_whiteglove
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export $(grep -v '^#' .env | xargs)
python3 app.py
```

Open:

```bash
http://127.0.0.1:5080
```

## Suggested production hosting

- Run behind Apache or Nginx with TLS
- Replace the Flask dev server with gunicorn
- Keep the scheduler process always on using systemd
- Store credentials in environment variables or a secret manager
- If you already operate other Pexip automation apps, place this under its own systemd unit and path

## Files

- `app.py` - backend API, SQLite storage, scheduler tick loop, split-host Pexip integration
- `templates/index.html` - polished shell and page layout
- `static/styles.css` - visual design
- `static/app.js` - timeline rendering and meeting actions
- `whiteglove.db` - created on first run

## Next improvements you may want

- chair/guest role selection per endpoint
- customer name, PIN, or operator fields
- overlap protection and conflict detection
- recurrence
- audit log/history panel
- move from polling to websocket or event sink integration
- direct conference existence validation before schedule save
- per-meeting host PIN support if your workflow needs it


## Apache note
Use a dedicated WSGI daemon process that points to the app virtual environment, and serve the app under `/cklabScheduler`. The frontend now derives its API base from `request.script_root`, so it will work correctly when mounted under `/cklabScheduler` instead of calling the global `/api/` proxy.
