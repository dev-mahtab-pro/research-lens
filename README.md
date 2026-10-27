# ResearchLens — AI Research Paper Analyzer

A powerful web application that uses OpenRouter to analyze research papers in 4 ways.

 Quick Start

 1. Install Dependencies
```bash
pip install -r requirements.txt
```

 2. Set Your API Key (optional)
You can either:
- Set it as an environment variable: `export OPENROUTER_API_KEY=sk-or-v1-...`
- Or enter it directly in the web UI (recommended — never stored)

 3. Run the App
```bash
python app.py
```

Then open your browser to: http://localhost:5000

## Platform authentication and deployment

The application now runs as a multi-user platform. Configure these environment variables before starting it:

```text
FLASK_SECRET_KEY=<long-random-secret>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<strong-password-at-least-12-characters>
SESSION_COOKIE_SECURE=true
DEFAULT_USER_TOKEN_LIMIT=100000
```

The administrator signs in at `/login` and manages accounts at `/admin`. Admin-issued user accounts have isolated research history, hashed passwords, active/disabled access, and token limits. Do not commit the SQLite database, secret key, or production credentials. In production, run behind HTTPS with a process manager/reverse proxy and move the database to a managed PostgreSQL service before operating at scale.

### Container deployment

Copy `.env.example` to `.env`, replace every placeholder, place TLS certificates at `deploy/certs/fullchain.pem` and `deploy/certs/privkey.pem`, then run:

```bash
docker compose up -d --build
```

The compose stack persists database and uploaded files in named volumes, serves HTTPS through Nginx, adds health checks, and runs Flask with Gunicorn. Create a backup from PowerShell with `./deploy/backup.ps1`. Never expose port 5000 directly to the internet.

---

 Features

4 Analysis Modes:

| Mode | Description |
|------|-------------|
|  Summarize | Plain language overview of the paper |
| Weaknesses | Critical review of gaps and limitations |
| New Ideas | Novel research directions and innovations |
| Roadmap | Step-by-step implementation plan |

 Full Report Mode
Run all 4 analyses at once and get a tabbed, downloadable report.

---

 Project Structure

```
research-tool/
├── app.py              # Flask backend
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Main UI
├── static/
│   ├── css/style.css   # Styling
│   └── js/main.js      # Frontend logic
└── uploads/            # Temp upload dir (auto-created)
```

---

 Getting an OpenRouter API Key

1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up or log in
3. Navigate to API Keys
4. Create a new key (starts with `sk-or-v1-...`)

---

 Supported File Formats
- PDF — Any research paper PDF
- TXT — Plain text format

Files up to ~10MB are supported. Papers longer than ~80,000 characters are automatically truncated for API limits.

---

 Large Paper & Dataset Support
- Papers that exceed the default window now trigger a chunk-and-merge workflow: each segment is summarized and then stitched into a single context before any of the four analyses run.
- These environment variables tune the workflow:
  - `ANALYSIS_MAX_CHARS` (default `250000`) limits how much text reaches the model.
  - `CHUNK_TARGET_SIZE` and `CHUNK_OVERLAP` tune the chunk sizing and overlap.
  - `MAX_CHUNK_SUMMARIES` caps how many chunks get summarized for very large inputs.
  - `CHAT_HISTORY_LIMIT` and `PAPER_CONTEXT_LIMIT` control how much per-paper chat history the app keeps.
- Structured parameters (problem, methodology, datasets, metrics, limitations, next steps, key terms) are extracted from the condensed context, so even a 30-page paper has a consistent view.

 Paper-Scoped Research Chat
- Every successful analysis now returns a `paper_id` plus a `chat_context`. The UI renders a ResearchBot pane that uses the extracted overview and parameters as its knowledge base.
- Ask follow-up questions about goals, data, experiments, or next steps without switching models, and the assistant will keep the conversation focused on the current paper.
- Use the*New conversation button to reset just the chat thread while the rest of your report stays visible.

---

Tips
- Use Full Report to get all 4 analyses at once for comprehensive research insight
- Download results as a Markdown file for your notes
- The Roadmap mode works best after running Weakness and New Ideas analyses first
