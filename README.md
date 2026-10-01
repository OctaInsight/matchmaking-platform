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

Participants can switch between Project owner, Investor and Audience at any time from the sidebar. The **Meet participants** page lists registered profiles and allows a direct request without an abstract. Run [direct_meetings_setup.sql](direct_meetings_setup.sql) once in Supabase SQL Editor to permit these direct requests. Existing abstract-linked meetings are preserved.

## Editing existing submissions

All signed-in users can open **My submissions** to edit submissions they own, regardless of the currently selected participant type. The super admin can edit any submission through **Super admin → Edit existing submissions** (or My submissions). The form supports title, category, thematic area, short summary, abstract up to 8,000 characters, keywords, support request and public poster/video links.

The editor verifies the signed-in account on every save and restricts ordinary users to their own owner ID. It uses the existing private server-side Supabase key; no new secret or SQL migration is needed. Only content fields can change. Record ID, author, event, slug, approval status, comments and meeting relationships are preserved. Edits to already approved content appear publicly immediately; the editor does not submit them for another review.

Run `python -m unittest test_submission_editor.py test_bulk_import.py` for local permission and validation checks without changing live data.

## Bulk Excel import (Super admin)

Open **Super admin → Bulk upload users and abstracts from Excel** and select **Download Excel import template**. The Import sheet uses the same participant/submission fields as the existing forms; Instructions explains each field. Only Participant name (also accepted as Username) and Participant email (also accepted as Email) are required. The email remains the sign-in username.

Use name/email only for users; fill submission fields to add contributions for existing accounts, new accounts, or both. New accounts use the supplied password or generate a random password when blank. Confirm password is optional; if supplied it must match. Existing accounts retain their passwords, profiles and participant roles. New participants default to Project owner. No passwords are saved in public application tables.

Event cells may contain an event ID or unique event title, or be blank to use the default event selected in the panel. Blank category defaults to Project idea. Blank Publish immediately sends the contribution for review; Yes publishes only when title and abstract are present. Missing titles get Contribution by [name]; missing abstracts get a labelled placeholder, and incomplete contributions stay unpublished for review. A provided title must have at least five characters and an abstract 30–8,000 characters. Public poster/video HTTPS links, thematic area, short summary, keywords and support request are optional.

Upload at most 500 filled rows in a .xlsx file under 10 MB, then **Validate and preview import**. The preview hides passwords and performs no writes. Fix any validation errors before proceeding. Verify email ownership for new participants and confirm the preview, then **Import previewed rows**.

The result table reports successes and errors per row. Accounts and submissions are separate operations: completed steps remain saved if a later step fails. Retry by previewing the file again. Identical bulk-imported contribution content has a deterministic primary key and is skipped, including during concurrent/retried inserts. Changing content creates a new contribution; the importer does not edit existing submissions.

Download the private new-account credentials CSV before closing the session and share credentials privately. Imported accounts are confirmed after organiser verification; the importer does not send welcome emails. Use **Clear import results and passwords from this session** after saving the credentials. The template is bundled with the code. No database migration is required.

Run local validation tests with `python -m unittest test_bulk_import.py`. These use a fake Supabase service, not live participant accounts. Account creation uses the [Supabase Auth Admin API](https://supabase.com/docs/reference/python/auth-admin-createuser).

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

## Live meeting alerts

Signed-in participants see incoming invitation counts and meetings in the next 24 hours directly below their name in the sidebar. A prominent main-page banner shows pending invitations and upcoming accepted meetings. A Streamlit fragment checks every 30 seconds while the browser session is connected; it updates only the alert area and does not reload forms or renew the eight-hour idle login. Alerts are explicitly scoped to the signed-in participant, including super admins. Accepted meetings still in progress are included until their end time.

New invitations, newly accepted meetings and meetings entering the 24-hour window trigger balloons and a toast once per event in the current browser session. **Enable notification sound** is optional and requires a click in the browser. It plays a test ping, then a short ping for new alerts; **Mute notification sound** turns it off. Refreshing/reopening the page may require enabling sound again. Browser autoplay/iframe restrictions can block sound; the visual alerts continue to work. Alerts are not background notifications when the app is closed. No database migration is needed.

References: [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment), [browser audio autoplay policy](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay).

## Password recovery

Admin-assigned passwords are normal sign-in passwords: they do not expire and no forced change is applied. Signed-in participants can optionally use **Change my password** in the sidebar to choose another password.

The email address is the sign-in name. The login page provides Sign in and Create account only; the Forgot password tab and reset-link request form are removed for this pilot. Signup confirmation and confirmation-email resend remain available. Existing recovery callbacks are retained for links already issued.

Keep `https://octa-matchmaking.streamlit.app/` in Supabase **Authentication → URL Configuration → Redirect URLs** for signup confirmation.

## Email delivery

Supabase's built-in email sender has a very low project-wide limit. Before inviting conference participants, configure a custom SMTP provider in **Supabase → Authentication → SMTP Settings** and test signup/confirmation with a second account. Do not disable email confirmation merely to bypass the sending limit.

The app sends the recipient an invitation email after saving a new meeting request. It includes the requester’s name, purpose, proposed time in UTC, current pending-request count, and a link to the app. The requester also receives a confirmation. Earlier requests do not trigger emails retroactively. Emails are sent when the request is created, not as a scheduled digest. Email failures do not undo the saved request.

### Resend (preferred)

Add these to Streamlit **App settings → Secrets**:

```toml
RESEND_API_KEY = "re_YOUR_PRIVATE_KEY"
RESEND_FROM_EMAIL = "matchmaking@conference.cloudearthi.com"
```

Verify the sender domain in Resend and give the key sending access to that domain. The app uses Resend’s HTTPS API with a 15-second timeout and per-request idempotency keys. Resend takes priority over SMTP when RESEND_API_KEY is present. The API key is never shown in diagnostics or committed to GitHub. No new package or database migration is required. Recipient email addresses are resolved privately through Supabase Auth using the existing server-side SUPABASE_SECRET_KEY (or legacy SUPABASE_SERVICE_ROLE_KEY), after checking the saved meeting belongs to the authenticated requester.

In **Super admin → Email diagnostics**, use **Send test email to me**. A success means the provider accepted the email, not that inbox delivery is guaranteed. Check the inbox/spam folder and Resend dashboard for delivery status. Next, create a meeting request between two controlled participant accounts and check the recipient invitation, pending count and requester confirmation. On a provider limit or error, the request remains available in My meetings; the app does not retry automatically.

### Accepted-meeting notifications

When the recipient accepts a pending request, the app first saves the acceptance and then emails both participants. The email contains the accepting participant’s name, purpose, UTC start/end time, the same Jitsi link shown in My meetings, and an attached .ics calendar event with a one-hour reminder. Calendar clients convert its UTC times to their configured local time zone. The attachment is an add-to-calendar event; participants open/import it, and calendar replies do not change the app’s meeting status.

Only the saved meeting’s authenticated recipient can trigger acceptance notifications. A conditional pending-to-accepted update avoids sending again from a stale Accept button. If sending fails, the acceptance remains saved; the recipient can use **Retry acceptance emails** on the accepted meeting. Resend suppresses repeat submissions with the same acceptance idempotency key for 24 hours. SMTP retries or retries after that period may send another email. No database migration is needed.

To test: create a future meeting between two accounts you control, accept it as the recipient, check both inboxes, open the attachment and verify the time, reminder and call link. Existing accepted meetings are not emailed automatically.

### SMTP fallback

**Streamlit does not automatically inherit Supabase SMTP settings.** If no Resend key is configured, existing SMTP delivery remains available:

```toml
SMTP_HOST = "smtp.example.com"
SMTP_PORT = 587
SMTP_USERNAME = "your-smtp-username"
SMTP_PASSWORD = "your-smtp-password"
SMTP_FROM = "Matchmaking <meetings@your-verified-domain.example>"
```

Port 587 uses STARTTLS; port 465 uses TLS from connection start. The sender must be permitted by the provider. Supabase signup and password-reset emails continue to use Supabase Auth’s separately configured email service.

Reference: [Resend Send Email API](https://resend.com/docs/api-reference/emails/send-email).

## Jitsi calls

When a recipient accepts an online meeting request, both participants see the same private-looking, hard-to-guess Jitsi room in **My meetings**. They open the full call in a new tab. The first participant must sign in with Jitsi's Log-in button to start the room; the other can then join. Public meet.jit.si embedded calls are demo-only and disconnect after five minutes, so the app does not embed them. An in-app production call would require Jitsi as a Service or a self-hosted Jitsi deployment. Anyone given the room URL can join, so share it only with the intended participants.

## Admin removal tools

Run [admin_removal_setup.sql](admin_removal_setup.sql) in the existing Supabase SQL Editor. Event admins can remove a whole submission or just its poster/video link within their assigned events, including approved submissions. Super admins have the same powers for all events and can delete an event or user. Each destructive action requires confirmation. An event deletion removes its submissions and associated comments/meetings while preserving participant accounts. A user deletion removes their account, owned submissions, authored comments/replies and meetings. The super-admin account cannot be deleted. SQL operations are transactional: unexpected foreign-key dependencies roll back the operation. External poster/video files are never deleted from their hosts.
