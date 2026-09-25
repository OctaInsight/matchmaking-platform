# Matchmaking Platform

A Streamlit app for Project Brokerage and Scale2Connect Matchmaking using the existing Supabase schema. Participants can sign in, submit abstracts for approval, browse approved submissions, request meetings, and comment. Authors can accept or decline meeting requests.

## Deployment

1. Use your existing Supabase project and tables: `profiles`, `submissions`, `comments`, and `meeting_requests`.
2. In **Authentication → URL Configuration**, allow `https://octa-matchmaking.streamlit.app/` as a redirect URL. If this project only serves this app, set it as Site URL too.
3. In Streamlit Community Cloud, deploy branch `main`, file `app.py`.
4. In **App settings → Secrets**, enter:
   ```toml
   SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
   SUPABASE_ANON_KEY = "sb_publishable_YOUR_KEY"
   ```
5. Streamlit installs dependencies automatically from `requirements.txt`.

**Do not run the old `database.sql` against your existing project.** That initial prototype schema is not compatible with your current database and has been removed from this repository.

## Data flow

A new abstract is stored in `submissions` with status `submitted`. Only `approved` submissions appear in public browsing. Approval must be carried out in your existing review process or Supabase dashboard. Comments are stored in `comments`. Meeting requests use `proposed_start` and `proposed_end` in UTC. The requester can cancel a pending request and the recipient can accept or decline it.

Poster and YouTube media use public URLs. The existing meeting table does not include a call link, so online calls are not yet integrated. Jury scoring and conference operations are deferred.

Never put a Supabase secret or service-role key into the app or repository. The publishable key works with your existing row level security policies.
