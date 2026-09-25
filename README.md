# Matchmaking Platform

Streamlit application for event-based abstract submission, review and matchmaking.

## Roles

| Role | What they can do |
| --- | --- |
| Super admin | Only the verified Supabase account `octainsight@gmail.com`: create events, assign/remove event sub-admins, review any event, and assign older abstracts to an event. |
| Event sub-admin | One or more registered users per event: review that event's submitted abstracts and approve or reject them. |
| Project owner | Select an event, submit an abstract, browse approved abstracts, comment and request meetings. |
| Investor | Browse approved abstracts, comment and request meetings. |
| Audience | Browse approved abstracts, comment and request meetings. |

## One-time database setup

In the existing Supabase project's **SQL Editor**, run [multi_role_setup.sql](multi_role_setup.sql) once. It keeps the existing `profiles`, `submissions`, `comments`, and `meeting_requests` tables. It adds event tables, participant types, guarded review functions and an `event_id` column to submissions. No manual admin role assignment is required: the review functions recognize the verified `octainsight@gmail.com` account by its Supabase Auth user ID and email.

The earlier [admin_setup.sql](admin_setup.sql) is superseded. Do not run it after this migration. If you ran it earlier, the new migration retires its global review functions.

Sign in to the app as `octainsight@gmail.com`, open **Super admin**, create an event, then enter the registered email address of each person who should be its sub-admin. Sub-admins can review only their assigned events. Older submitted abstracts can be assigned to an event on this page.

## Streamlit deployment

Deploy `app.py` from branch `main`. In **App settings → Secrets**:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_YOUR_KEY"
```

Allow `https://octa-matchmaking.streamlit.app/` in Supabase **Authentication → URL Configuration → Redirect URLs**. Streamlit installs packages from `requirements.txt` automatically. Never put a secret or service-role key into Streamlit or GitHub.

Project owners submit abstracts with status `submitted`. Event admins approve or reject them. Only approved abstracts appear in browsing. Meeting times are entered in UTC. Media currently uses public poster and YouTube URLs. Online call links and conference scoring remain future work.
