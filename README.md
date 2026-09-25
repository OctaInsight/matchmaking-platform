# Matchmaking Platform

A first Streamlit app for Project Brokerage and Scale2Connect Matchmaking. Participants can create accounts, publish abstracts with poster and YouTube links, browse submissions, request meetings, and leave feedback. Authors can accept or decline requests and share a call link. Jury scoring and conference operations are deferred.

## Setup

1. In your Supabase project, open **SQL Editor**, paste `database.sql`, and run it once.
2. In **Authentication → Providers**, enable Email. In **Authentication → URL Configuration**, add `https://octa-matchmaking.streamlit.app/` to **Redirect URLs**. If this Supabase project is only for this app, also set **Site URL** to that address. The app requests that redirect for signup and resend confirmation emails. Supabase may fall back to Site URL if the requested URL is not allowlisted.
3. In Streamlit Community Cloud, choose **New app**, select this repository, branch `main`, and main file `app.py`.
4. Under the app's **Advanced settings → Secrets**, enter:
   ```toml
   SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
   SUPABASE_ANON_KEY = "YOUR-PUBLISHABLE-OR-ANON-KEY"
   ```
5. Deploy. Streamlit installs packages from `requirements.txt` automatically. No local Python environment is needed.

For local use only, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, fill in the values, install requirements, and run `streamlit run app.py`. Never commit real keys or a service_role key.

Posters are accepted as a public image or PDF URL. Videos must be public YouTube URLs. Meeting requests use the time zone selected by each requester; authors see the stored UTC time. Calls use links supplied by the author, so no video service integration is needed.

## Security

Supabase row level security protects data for signed-in users. Names, organisations, abstracts, and feedback are visible to signed-in participants. Meeting messages and links are visible only to the requester and author. Treat submitted poster and video URLs as public content.
