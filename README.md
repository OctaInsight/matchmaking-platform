# Matchmaking Platform

Streamlit application for event-based project ideas, oral presentations, posters and matchmaking.

## Roles

| Role | What they can do |
| --- | --- |
| Super admin | Only the verified Supabase account `octainsight@gmail.com`: create events, participant accounts with passwords, and submissions on behalf of authors; assign/remove event sub-admins, review any event, and assign older abstracts to an event. |
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

The super admin can create a participant account with a password and add an abstract with optional poster and video links for that author. Confirm that the email belongs to the participant before marking the account as confirmed. Share the password privately; the app does not email it. The submission can be published immediately or sent to the event review queue. These admin tools use a separate server-side Supabase client and do not require a database migration.

Participants choose Project owner, Investor or Audience during initial account setup. The sidebar does not include a role-switching dropdown. The **Meet participants** page lists registered profiles and allows a direct request without an abstract. Run [direct_meetings_setup.sql](direct_meetings_setup.sql) once in Supabase SQL Editor to permit these direct requests. Existing abstract-linked meetings are preserved.

## Streamlit deployment

Deploy `app.py` from branch `main`. In **App settings → Secrets**:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_YOUR_KEY"
SUPABASE_SECRET_KEY = "sb_secret_YOUR_SERVER_KEY"
```

The secret key enables super-admin account and submission creation. If your project has a legacy `service_role` key instead, set `SUPABASE_SERVICE_ROLE_KEY` in Secrets. These keys bypass row-level security: keep them only in Streamlit Secrets, never in GitHub or browser code. Allow `https://octa-matchmaking.streamlit.app/` in Supabase **Authentication → URL Configuration → Redirect URLs**. Streamlit installs packages from `requirements.txt` automatically.

Project owners submit project ideas, oral presentations or posters with status `submitted`. Event admins approve or reject them. Only approved submissions appear in browsing. Meeting times are entered and displayed in each visitor’s browser time zone, and stored as UTC.

## Staying signed in

Run [persistent_sessions_setup.sql](persistent_sessions_setup.sql) once in Supabase SQL Editor. The existing server-side Supabase secret key enables encrypted session storage; no new secret is required. Login survives refreshes and closing/reopening the browser for eight hours after the last app interaction. Each app interaction renews the idle timeout. Explicit sign-out removes the stored session and clears the browser cookie. Cookies contain only a random session ID, while access/refresh tokens are encrypted in a private database table accessible only to the service role. Changing the server key invalidates saved sessions. Browser settings that block cookies require signing in again after refresh. The old `COOKIE_PASSWORD` setting is unused.

## Password recovery

Admin-assigned passwords are normal sign-in passwords: they do not expire and no forced change is applied. Signed-in participants can optionally use **Change my password** in the sidebar to choose another password.

The email address is the sign-in name. **Sign in or create account → Forgot password** requests a standard Supabase password reset link. The response is intentionally the same whether the address has an account, to avoid exposing the list of registered participants. Supabase's default email template is supported: after its link returns to the app, the small `recovery_fragment` browser component reads the recovery session in the URL fragment, clears the fragment, and opens the new-password form. If browser restrictions prevent automatic capture, the user can request a fresh email and copy its original link (without opening it) into the fallback form under Forgot password, along with their account email. The fallback validates the Supabase host and recovery type and verifies the token hash. Older token-hash recovery links are still accepted if already configured.

No Supabase email-template change is needed. Keep `https://octa-matchmaking.streamlit.app/` in **Authentication → URL Configuration → Redirect URLs**. The optional [recovery_email_template.html](recovery_email_template.html) is not used by this flow. Supabase must be able to deliver Auth emails; its built-in sender is limited and a custom SMTP provider is recommended for event use.

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

## Admin removal tools

Run [admin_removal_setup.sql](admin_removal_setup.sql) in the existing Supabase SQL Editor. Event admins can remove a whole submission or just its poster/video link within their assigned events, including approved submissions. Super admins have the same powers for all events and can delete an event or user. Each destructive action requires confirmation. An event deletion removes its submissions and associated comments/meetings while preserving participant accounts. A user deletion removes their account, owned submissions, authored comments/replies and meetings. The super-admin account cannot be deleted. SQL operations are transactional: unexpected foreign-key dependencies roll back the operation. External poster/video files are never deleted from their hosts.
