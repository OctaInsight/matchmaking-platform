# Matchmaking Platform

Streamlit application for event-based project ideas, oral presentations, posters and matchmaking.

## Roles

| Role | What they can do |
| --- | --- |
| Super admin | Only the verified Supabase account `octainsight@gmail.com`: create events, assign/remove event sub-admins, review any event, and assign older abstracts to an event. |
| Event sub-admin | One or more registered users per event: review that event's submitted abstracts and approve or reject them. |
| Project owner | Select an event, submit an abstract, browse approved abstracts, comment and request meetings with any participant. |
| Investor | Browse approved abstracts, comment and request meetings with any participant. |
| Audience | Browse approved abstracts, comment and request meetings with any participant. |

## Public gallery and guest comments

Apply [public_gallery_setup.sql](public_gallery_setup.sql) in the **existing Supabase SQL Editor** after `multi_role_setup.sql`. It adds a category and optional support request to submissions, public functions that return only approved submissions, and a separate guest-comment table. Existing submissions default to **Project idea**; they are not deleted. Guest email addresses remain private in the database and are not returned by the gallery or comment functions.

Visitors can search by category, title, author and keyword. Each gallery card opens a full-width page with Abstract, Poster, Video, Comments and Meeting tabs. Comments are shown on that page, not beneath the gallery cards. Guest comments use a name and private email. Run [guest_comments_immediate.sql](guest_comments_immediate.sql) after `public_gallery_setup.sql` to publish guest comments immediately and publish earlier pending comments. The posting limit of five comments per email address per hour remains. Signed-in comments also appear immediately. Only a signed-in owner can reply to comments on their own approved submission; meetings continue to require sign-in.

Poster and video fields accept public HTTPS links. Authors should upload poster files to Google Drive or a comparable public host and videos to YouTube or another publicly accessible service, then paste links. Direct image links and supported Google Drive file links can render a thumbnail; PDF posters show an icon and an **Open public poster** link.

## One-time database setup

In the existing Supabase project's **SQL Editor**, run [multi_role_setup.sql](multi_role_setup.sql) once. It keeps the existing `profiles`, `submissions`, `comments`, and `meeting_requests` tables. It adds event tables, participant types, guarded review functions and an `event_id` column to submissions. No manual admin role assignment is required: the review functions recognize the verified `octainsight@gmail.com` account by its Supabase Auth user ID and email.

The earlier global-admin setup has been removed. If you ran it earlier, this migration retires its global review functions.

Sign in to the app as `octainsight@gmail.com`, open **Super admin**, create an event, then enter the registered email address of each person who should be its sub-admin. Sub-admins can review only their assigned events. Older submitted abstracts can be assigned to an event on this page.

Participants can switch between Project owner, Investor and Audience at any time from the sidebar. The **Meet participants** page lists registered profiles and allows a direct request without an abstract. Run [direct_meetings_setup.sql](direct_meetings_setup.sql) once in Supabase SQL Editor to permit these direct requests. Existing abstract-linked meetings are preserved.

## Streamlit deployment

Deploy `app.py` from branch `main`. In **App settings → Secrets**:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_YOUR_KEY"
```

Allow `https://octa-matchmaking.streamlit.app/` in Supabase **Authentication → URL Configuration → Redirect URLs**. Streamlit installs packages from `requirements.txt` automatically. Never commit a service-role key to GitHub.

Project owners submit project ideas, oral presentations or posters with status `submitted`. Event admins approve or reject them. Only approved submissions appear in browsing. Meeting times are entered and displayed in each visitor’s browser time zone, and stored as UTC.

## Staying signed in

Session restoration through the cookie component is temporarily disabled while the public gallery is stabilised. A full browser refresh signs users out; they can sign in again from the sidebar. The `COOKIE_PASSWORD` secret is currently unused.

## Password recovery

The email address is the sign-in name. **Sign in or create account → Forgot password** requests a Supabase password reset email. The response is intentionally the same whether the address has an account, to avoid exposing the list of registered participants. A valid reset link returns to the app; the user enters their email to verify the one-time token, then sets a new password.

In the existing Supabase project, open **Authentication → Email Templates → Reset Password** and replace that template's body with [recovery_email_template.html](recovery_email_template.html). The link uses `{{ .RedirectTo }}` and `{{ .TokenHash }}`. Keep `https://octa-matchmaking.streamlit.app/` in **Authentication → URL Configuration → Redirect URLs**. Send a test reset request only after saving the template. Supabase must be able to deliver Auth emails; its built-in sender is limited and a custom SMTP provider is recommended for event use.

## Email delivery

Supabase's built-in email sender has a very low project-wide limit. Before inviting conference participants, configure a custom SMTP provider in **Supabase → Authentication → SMTP Settings** and test signup/confirmation with a second account. Do not disable email confirmation merely to bypass the sending limit.

The app sends the requester a confirmation email after saving a new meeting request. In **Super admin → Email diagnostics**, use **Send test email to me** to check the SMTP configuration and delivery. Earlier requests do not trigger an email retroactively. **Streamlit does not automatically inherit Supabase SMTP settings.** Add these to Streamlit **App settings → Secrets**, using credentials from your email provider:

```toml
SMTP_HOST = "smtp.example.com"
SMTP_PORT = 587
SMTP_USERNAME = "your-smtp-username"
SMTP_PASSWORD = "your-smtp-password"
SMTP_FROM = "Matchmaking <meetings@your-verified-domain.example>"
```

Port 587 uses STARTTLS; port 465 uses TLS from connection start. The sender address must be permitted by your SMTP provider. Without these settings the request is still saved, but the app cannot send its confirmation email. Never commit these values to GitHub.

## Jitsi calls

When a recipient accepts an online meeting request, both participants see the same private-looking, hard-to-guess Jitsi room in **My meetings**. They open the full call in a new tab. The first participant must sign in with Jitsi's Log-in button to start the room; the other can then join. Public meet.jit.si embedded calls are demo-only and disconnect after five minutes, so the app does not embed them. An in-app production call would require Jitsi as a Service or a self-hosted Jitsi deployment. Anyone given the room URL can join, so share it only with the intended participants.
