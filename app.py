import re
import json
import base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html import escape
from logo_asset import LOGO_BASE64
import uuid
import smtplib
import socket
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse
from urllib.parse import parse_qs
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions
from persistent_sessions import SessionStore
from bulk_import_ui import bulk_import_panel
from submission_editor import submission_editor

st.set_page_config(page_title="Matchmaking Platform", page_icon="🤝", layout="wide")
st.title("Project / Poster / Abstract Matchmaking")
APP_URL = "https://octa-matchmaking.streamlit.app/"
meeting_alert_sound = components.declare_component(
    "meeting_alert_sound", path=str(Path(__file__).parent / "alert_sound")
)
read_recovery_fragment = components.declare_component(
    "read_recovery_fragment", path=str(Path(__file__).parent / "recovery_fragment")
)


def recovery_tokens_from_url(value):
    """Accept only a Supabase recovery callback, never a plain sign-in link."""
    try:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != urlparse(APP_URL).netloc:
            return None
        fields = parse_qs(parsed.fragment)
        if fields.get("type") != ["recovery"]:
            return None
        access = fields.get("access_token", [""])[0]
        refresh = fields.get("refresh_token", [""])[0]
        if not access or not refresh:
            return None
        return {"access_token": access, "refresh_token": refresh}
    except (TypeError, ValueError):
        return None


def recovery_hash_from_email_link(value, supabase_url):
    """Extract a recovery token hash from Supabase's default email link."""
    try:
        parsed = urlparse(value)
        expected = urlparse(supabase_url)
        if parsed.scheme != "https" or parsed.netloc != expected.netloc or parsed.path != "/auth/v1/verify":
            return None
        fields = parse_qs(parsed.query)
        if fields.get("type") != ["recovery"]:
            return None
        token_hash = fields.get("token", [""])[0]
        return token_hash if 8 <= len(token_hash) <= 512 else None
    except (TypeError, ValueError):
        return None

def sidebar_footer():
    st.sidebar.divider()
    st.sidebar.markdown(
        '<div style="background:#101820;padding:12px;border-radius:8px;text-align:center">'
        '<img alt="Octa Insight" width="140" src="data:image/png;base64,'
        + LOGO_BASE64 + '"></div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("**Created by Octa System**")
    st.sidebar.caption("Pilot test: some functions may not work as expected. "
                       "Please contact OctaInsight@gmail.com if you find a problem.")
    st.sidebar.caption("© 2026 Octa Insight AS")


login_cookie = components.declare_component(
    "login_cookie", path=str(Path(__file__).parent / "login_cookie")
)
_session_store = None


def session_store():
    global _session_store
    if _session_store is None:
        key = st.secrets.get("SUPABASE_SECRET_KEY") or st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
        if not key:
            return None
        admin = create_client(st.secrets["SUPABASE_URL"], key,
            options=SyncClientOptions(auto_refresh_token=False, persist_session=False))
        _session_store = SessionStore(admin, key)
    return _session_store


def sync_login_cookie():
    sid = st.session_state.get("login_session_id")
    command = ({"action": "clear", "id": "logout"} if st.session_state.get("cookie_logout")
        else {"action": "set", "value": sid, "id": sid} if sid else {})
    result = login_cookie(command=command, default=None, key="login_cookie")
    if result is None and not st.session_state.get("skip_login_cookie"):
        st.info("Checking your saved sign-in…")
        if st.button("Continue without remembering sign-in"):
            st.session_state.skip_login_cookie = True
            st.rerun()
        st.stop()
    if result and not result.get("available", True):
        st.session_state.session_warning = "This browser blocked the sign-in cookie. Refreshes may require signing in again."
    if result and not sid and not st.session_state.get("cookie_logout"):
        value = result.get("value", "")
        if len(value) == 64:
            st.session_state.login_session_id = value


def remember_tokens(tokens, uid):
    st.session_state.tokens = tokens
    st.session_state.cookie_logout = False
    try:
        store = session_store()
        if store:
            sid = store.save(tokens, uid, st.session_state.get("login_session_id"))
            st.session_state.login_session_id = sid
            st.session_state.pop("session_warning", None)
        else:
            st.session_state.session_warning = "Saved sign-in needs the server-side Supabase admin key."
    except ValueError:
        raise
    except Exception:
        st.session_state.session_warning = "Saved sign-in is not configured. Run persistent_sessions_setup.sql in Supabase SQL Editor."


def forget_tokens():
    sid = st.session_state.pop("login_session_id", None)
    st.session_state.pop("tokens", None)
    st.session_state.cookie_logout = True
    if sid:
        try:
            store = session_store()
            if store:
                store.revoke(sid)
        except Exception:
            pass




def client():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_ANON_KEY"]
    except (KeyError, FileNotFoundError):
        st.error("Supabase settings are missing. Add SUPABASE_URL and SUPABASE_ANON_KEY to Streamlit secrets.")
        st.stop()
    db = create_client(url, key)
    tokens = st.session_state.get("tokens")
    sid = st.session_state.get("login_session_id")
    if sid:
        try:
            store = session_store()
            record = store.read(sid) if store else None
            if not record:
                forget_tokens()
                tokens = None
            else:
                tokens = record["tokens"]
        except Exception:
            forget_tokens()
            tokens = None
    if tokens:
        try:
            response = db.auth.set_session(tokens["access_token"], tokens["refresh_token"])
            if response.session:
                remember_tokens({
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                }, response.session.user.id)
        except Exception:
            forget_tokens()
            st.rerun()
    return db


def user_id(db):
    if not st.session_state.get("tokens"):
        return None
    try:
        return db.auth.get_user().user.id
    except Exception:
        forget_tokens()
        st.rerun()


def valid_url(value, hosts=None):
    if not value:
        return True
    p = urlparse(value.strip())
    return p.scheme == "https" and bool(p.netloc) and (
        hosts is None or p.hostname in hosts
    )


def poster_thumbnail(url):
    if not url:
        return None
    if re.search(r"\.(png|jpe?g|webp)(\?.*)?$", url, re.I):
        return url
    parsed = urlparse(url)
    if parsed.hostname in ("drive.google.com", "www.drive.google.com"):
        match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
        if match:
            return "https://drive.google.com/thumbnail?id=" + match.group(1) + "&sz=w400"
    return None


def save_profile(db, uid):
    row = db.table("profiles").select("id").eq("id", uid).execute().data
    if row:
        return
    st.info("Complete your participant profile.")
    with st.form("profile"):
        name = st.text_input("Full name", max_chars=120)
        organisation = st.text_input("Organisation", max_chars=160)
        bio = st.text_area("Short profile (optional)", max_chars=1000)
        submitted = st.form_submit_button("Save profile")
    if submitted:
        if len(name.strip()) < 2:
            st.info("Add your full name to continue.")
        else:
            try:
                db.table("profiles").insert({
                    "id": uid, "full_name": name.strip(),
                    "organisation": organisation.strip(), "biography": bio.strip()
                }).execute()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save profile: {exc}")
    st.stop()


def auth_screen(db):
    st.caption("Your email address is your sign-in name.")
    sign_in, sign_up = st.tabs(["Sign in", "Create account"])
    with sign_in:
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            try:
                res = db.auth.sign_in_with_password({"email": email.strip(), "password": password})
                remember_tokens({
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }, res.session.user.id)
                st.rerun()
            except Exception as exc:
                st.error(f"Sign in failed: {exc}")
    with sign_up:
        with st.form("signup"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password (at least 8 characters)", type="password", key="signup_password")
            submitted = st.form_submit_button("Create account")
        if submitted:
            if len(password) < 8:
                st.info("Choose a password with at least 8 characters.")
            else:
                try:
                    res = db.auth.sign_up({
                        "email": email.strip(), "password": password,
                        "options": {"email_redirect_to": APP_URL},
                    })
                    if res.session:
                        remember_tokens({
                            "access_token": res.session.access_token,
                            "refresh_token": res.session.refresh_token,
                        }, res.session.user.id)
                        st.rerun()
                    st.success("Account created. Check your email to confirm it, then sign in.")
                except Exception as exc:
                    if "rate limit" in str(exc).lower() or "over_email_send_rate_limit" in str(exc).lower():
                        st.info("Confirmation emails are temporarily limited by Supabase. Please try again later. The event organiser is setting up reliable email delivery.")
                    else:
                        st.error(f"Account creation failed: {exc}")
    st.markdown("**Confirmation email expired?**")
    resend_email = st.text_input("Account email", key="resend_email")
    if st.button("Resend confirmation email"):
        if not resend_email.strip():
            st.info("Enter your account email above.")
        else:
            try:
                db.auth.resend({
                    "type": "signup", "email": resend_email.strip(),
                    "options": {"email_redirect_to": APP_URL},
                })
                st.success("If confirmation is still needed, check your inbox for a new email.")
            except Exception as exc:
                st.error(f"Could not resend confirmation: {exc}")


def password_recovery_screen(db):
    st.subheader("Reset your password")
    token_hash = st.query_params.get("recovery_token")
    if not st.session_state.get("recovery_tokens"):
        st.caption("Enter the email address that received the reset link.")
        with st.form("verify_recovery"):
            email = st.text_input("Account email", max_chars=254)
            verify = st.form_submit_button("Continue")
        if verify:
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
                st.info("Enter a valid email address.")
                return
            try:
                response = db.auth.verify_otp({
                    "email": email.strip(), "token_hash": token_hash, "type": "recovery",
                })
                if not response.session:
                    raise ValueError("Recovery session was not created")
                st.session_state.recovery_tokens = {
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                }
                del st.query_params["recovery_token"]
                st.rerun()
            except Exception:
                st.info("This reset link is invalid or expired. Request a new link from Forgot password.")
        if st.button("Request a new link"):
            if token_hash:
                del st.query_params["recovery_token"]
            st.session_state.public_page = "Sign in or create account"
            st.rerun()
        return
    with st.form("set_new_password"):
        password = st.text_input("New password (at least 8 characters)", type="password")
        again = st.text_input("Confirm new password", type="password")
        change = st.form_submit_button("Set new password")
    if change:
        if len(password) < 8 or password != again:
            st.info("Use at least 8 characters and enter the same password twice.")
            return
        try:
            tokens = st.session_state.recovery_tokens
            session = db.auth.set_session(tokens["access_token"], tokens["refresh_token"])
            if session.session:
                st.session_state.recovery_tokens = {
                    "access_token": session.session.access_token,
                    "refresh_token": session.session.refresh_token,
                }
            db.auth.update_user({"password": password})
            try:
                db.auth.sign_out()
            except Exception:
                pass
            st.session_state.pop("recovery_tokens", None)
            forget_tokens()
            st.session_state.reset_done = True
            st.session_state.public_page = "Sign in or create account"
            st.rerun()
        except Exception:
            st.error("Could not change the password. Request a fresh reset link and try again.")


def publish(db, uid, events):
    st.subheader("Submit a project idea, oral presentation or poster")
    with st.form("abstract"):
        event_titles = {e["title"]: e["id"] for e in events if e.get("is_active")}
        if not event_titles:
            st.info("No event is open for submissions yet.")
            st.stop()
        event_title = st.selectbox("Event", list(event_titles))
        category = st.selectbox("Category", ["Project idea", "Oral presentation", "Poster"])
        title = st.text_input("Title", max_chars=200, help="At least 5 characters.")
        thematic_area = st.text_input("Thematic area (optional)", max_chars=160)
        summary = st.text_input("Short summary (optional)", max_chars=300)
        body = st.text_area("Abstract", height=180, max_chars=8000, help="At least 30 characters.")
        keywords = st.text_input("Keywords", max_chars=300)
        support_request = st.text_area("Technical help, investment or collaboration sought (optional)", max_chars=2000)
        st.caption("Upload your poster to Google Drive or a similar service and make the link public. Upload your two-minute video to YouTube or another open platform. Paste the links here; check each in a private browser window.")
        poster = st.text_input("Public poster URL (image or PDF, optional)")
        video = st.text_input("Public two-minute video URL (optional)")
        submitted = st.form_submit_button("Submit for approval")
    if submitted:
        if len(title.strip()) < 5 or len(body.strip()) < 30:
            st.info("To submit, add a title of at least 5 characters and an abstract of at least 30 characters.")
        elif not valid_url(poster) or not valid_url(video):
            st.info("Check the poster and video links: both need public HTTPS addresses.")
        else:
            try:
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70] + "-" + __import__("uuid").uuid4().hex[:8]
                db.table("submissions").insert({
                    "owner_id": uid, "event_id": event_titles[event_title],
                    "title": title.strip(), "slug": slug,
                    "abstract_text": body.strip(), "short_summary": summary.strip() or None,
                    "thematic_area": thematic_area.strip() or None,
                    "keywords": [word.strip() for word in keywords.split(",") if word.strip()],
                    "poster_url": poster.strip() or None, "video_url": video.strip() or None,
                    "presentation_category": category.lower().replace(" ", "_"),
                    "support_request": support_request.strip() or None,
                    "status": "submitted", "submitted_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                st.success("Your submission was sent for approval. It will appear publicly once approved.")
            except Exception as exc:
                st.error(f"Could not publish: {exc}")


def smtp_missing_settings():
    if st.secrets.get("RESEND_API_KEY"):
        return [] if st.secrets.get("RESEND_FROM_EMAIL") else ["RESEND_FROM_EMAIL"]
    needed = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
    return [name for name in needed if not st.secrets.get(name)]


class EmailStageError(Exception):
    def __init__(self, stage, original):
        self.stage = stage
        self.original = original
        super().__init__(str(original))


def send_email(to_address, subject, body, idempotency_key=None, attachments=None):
    if st.secrets.get("RESEND_API_KEY"):
        sender = st.secrets.get("RESEND_FROM_EMAIL")
        if not sender:
            raise ValueError("Missing Streamlit Secret: RESEND_FROM_EMAIL")
        headers = {
            "Authorization": "Bearer " + st.secrets["RESEND_API_KEY"],
            "Content-Type": "application/json",
            "User-Agent": "OctaMatchmaking/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        payload = {"from": sender,
                   "to": to_address if isinstance(to_address, list) else [to_address],
                   "subject": subject, "text": body}
        if attachments:
            payload["attachments"] = [
                {"filename": filename, "content": base64.b64encode(content).decode("ascii")}
                for filename, content in attachments
            ]
        request = Request("https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
            if not result.get("id"):
                raise ValueError("Resend did not confirm acceptance of the email.")
            return result["id"]
        except HTTPError as exc:
            messages = {
                401: "Resend rejected the API key. Check RESEND_API_KEY.",
                403: "Resend denied sending. Verify the sender domain and the API key's sending permission.",
                422: "Resend rejected the email settings. Check the verified sender address.",
                429: "Resend's sending limit was reached. Check your Resend dashboard.",
            }
            raise ValueError(messages.get(exc.code,
                f"Resend returned HTTP {exc.code}. Check its dashboard logs.")) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError("Could not reach Resend. Check your Resend dashboard before retrying.") from None

    missing = smtp_missing_settings()
    if missing:
        raise ValueError("Missing Streamlit Secrets: " + ", ".join(missing))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = st.secrets["SMTP_FROM"]
    msg["To"] = ", ".join(to_address) if isinstance(to_address, list) else to_address
    msg.set_content(body)
    for filename, content in attachments or []:
        msg.add_attachment(content, maintype="text", subtype="calendar", filename=filename)
    host = st.secrets["SMTP_HOST"]
    port = int(st.secrets["SMTP_PORT"])
    stage = "connect"
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context()) as server:
                stage = "login"
                server.login(st.secrets["SMTP_USERNAME"], st.secrets["SMTP_PASSWORD"])
                stage = "send"
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                stage = "TLS"
                server.starttls(context=ssl.create_default_context())
                stage = "login"
                server.login(st.secrets["SMTP_USERNAME"], st.secrets["SMTP_PASSWORD"])
                stage = "send"
                server.send_message(msg)
    except Exception as exc:
        raise EmailStageError(stage, exc) from exc


def viewer_timezone():
    try:
        return ZoneInfo(st.context.timezone)
    except (AttributeError, TypeError, ValueError, ZoneInfoNotFoundError):
        offset = getattr(st.context, "timezone_offset", None)
        if offset is not None:
            return timezone(-timedelta(minutes=offset))
        return timezone.utc


def display_meeting_time(value, tz):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(tz).strftime("%d %b %Y, %H:%M %Z (UTC%z)")


def parse_meeting_time(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def meeting_call_url(row):
    if row.get("format") == "online":
        return "https://meet.jit.si/OctaMatchmaking" + row["id"].replace("-", "")
    return APP_URL


def calendar_invitation(row):
    def escape(value):
        return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    def stamp(value):
        return parse_meeting_time(value).strftime("%Y%m%dT%H%M%SZ")

    url = meeting_call_url(row)
    details = escape((row.get("purpose") or "") + "\nMeeting link: " + url)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OctaInsight//Matchmaking//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
        "UID:" + row["id"] + "@octa-matchmaking.streamlit.app",
        "DTSTAMP:" + stamp(row.get("created_at") or row["proposed_start"]),
        "DTSTART:" + stamp(row["proposed_start"]),
        "DTEND:" + stamp(row["proposed_end"]),
        "SUMMARY:Octa Matchmaking meeting", "STATUS:CONFIRMED", "SEQUENCE:0", "DESCRIPTION:" + details,
        "URL:" + url,
        "BEGIN:VALARM", "ACTION:DISPLAY",
        "DESCRIPTION:Your Octa Matchmaking meeting starts in one hour",
        "TRIGGER:-PT1H", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    # RFC 5545: fold long content lines at 75 octets without splitting UTF-8.
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")


def send_booking_email(db, recipient_name, start, end, meeting_id):
    user = db.auth.get_user().user
    if not user or not user.email:
        raise ValueError("Your account has no email address")
    send_email(
        user.email,
        "Your matchmaking meeting request",
        f"Your meeting request for {recipient_name} has been submitted.\n\n"
        f"Proposed time: {display_meeting_time(start.isoformat(), viewer_timezone())}–"
        f"{end.astimezone(viewer_timezone()).strftime('%H:%M')}\n\n"
        "The recipient still needs to accept it. You can check its status in My meetings.\n\n" + APP_URL,
        idempotency_key=f"meeting-{meeting_id}-confirmation",
    )


def send_invitation_email(db, uid, meeting_id):
    # Authorize against the saved row before using the privileged Auth lookup.
    rows = db.table("meeting_requests").select("*").eq("id", meeting_id).eq(
        "requester_id", uid).execute().data or []
    account = db.auth.get_user().user
    if not rows or not account or account.id != uid:
        raise ValueError("Could not verify the saved meeting request.")
    meeting = rows[0]
    server_key = st.secrets.get("SUPABASE_SECRET_KEY") or st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not server_key:
        raise ValueError("Recipient notifications require the server-side Supabase admin key.")
    admin = create_client(st.secrets["SUPABASE_URL"], server_key,
        options=SyncClientOptions(auto_refresh_token=False, persist_session=False))
    recipient = admin.auth.admin.get_user_by_id(meeting["recipient_id"]).user
    if not recipient or not recipient.email:
        raise ValueError("The recipient has no email address.")
    count = admin.table("meeting_requests").select("id", count="exact").eq(
        "recipient_id", meeting["recipient_id"]).eq("status", "pending").execute().count
    profiles = db.table("profiles").select("full_name").eq("id", uid).execute().data or []
    name = profiles[0].get("full_name") if profiles else None
    pending = (f"You currently have {count} pending meeting request(s)."
               if count is not None else "View your pending requests in My meetings.")
    send_email(recipient.email, "New matchmaking meeting invitation",
        f"{name or 'A participant'} has requested a meeting with you.\n\n"
        f"Purpose: {meeting['purpose']}\n\n"
        f"Proposed time (UTC): {meeting['proposed_start']} to {meeting['proposed_end']}\n\n"
        f"{pending}\n\nOpen the app, sign in and select My meetings to accept or decline. "
        "The app displays the time in your browser's time zone.\n\n" + APP_URL,
        idempotency_key=f"meeting-{meeting_id}-invitation")


def send_acceptance_email(db, uid, meeting_id):
    # Only the accepting recipient may notify participants of a saved acceptance.
    account = db.auth.get_user().user
    rows = db.table("meeting_requests").select("*").eq("id", meeting_id).eq(
        "recipient_id", uid).eq("status", "accepted").execute().data or []
    if not account or account.id != uid or not rows:
        raise ValueError("Could not verify the accepted meeting.")
    row = rows[0]
    key = st.secrets.get("SUPABASE_SECRET_KEY") or st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise ValueError("Acceptance emails require the server-side Supabase admin key.")
    admin = create_client(st.secrets["SUPABASE_URL"], key,
        options=SyncClientOptions(auto_refresh_token=False, persist_session=False))
    people = []
    for person_id in (row["requester_id"], row["recipient_id"]):
        person = admin.auth.admin.get_user_by_id(person_id).user
        if not person or not person.email:
            raise ValueError("A meeting participant has no email address.")
        people.append(person.email)
    profiles = db.table("profiles").select("full_name").eq("id", uid).execute().data or []
    name = profiles[0].get("full_name") if profiles else None
    body = (
        f"{name or 'The recipient'} has accepted the matchmaking meeting.\n\n"
        f"Purpose: {row['purpose']}\n\n"
        f"Start: {display_meeting_time(row['proposed_start'], timezone.utc)}\n"
        f"End: {display_meeting_time(row['proposed_end'], timezone.utc)}\n\n"
        f"Meeting link: {meeting_call_url(row)}\n\n"
        "Open the attached calendar invitation to add the meeting to your calendar. "
        "Your calendar displays the time in its local time zone and includes a one-hour reminder.\n\n"
    )
    if row.get("format") == "online":
        body += "The first participant must log in to Jitsi to start the room; the other can then join.\n\n"
    body += "Manage this meeting in My meetings: " + APP_URL
    # One provider request notifies both parties with the same event and link.
    return send_email(list(dict.fromkeys(people)), "Matchmaking meeting accepted",
        body, idempotency_key=f"meeting-{meeting_id}-accepted",
        attachments=[(f"octa-meeting-{meeting_id}.ics", calendar_invitation(row))])


def smtp_issue(exc):
    stage = exc.stage if isinstance(exc, EmailStageError) else None
    if isinstance(exc, EmailStageError):
        exc = exc.original
    prefix = f"At {stage}: " if stage else ""
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP login was rejected (code {exc.smtp_code}). Check the mailbox password or app password and enable SMTP authentication."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"The sender address was rejected (code {exc.smtp_code}). Check SMTP_FROM."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The email provider rejected the recipient address."
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "This SMTP server does not support the selected TLS method. Check the host and port."
    if isinstance(exc, ssl.SSLError):
        return "TLS negotiation failed. Check that port 465 uses SSL and port 587 uses STARTTLS."
    if isinstance(exc, socket.gaierror):
        return "The SMTP host name could not be found. Check SMTP_HOST."
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "The SMTP connection timed out. Check the host and port or ask the provider whether cloud servers can connect."
    if isinstance(exc, ConnectionRefusedError):
        return "The SMTP server refused the connection. Check the host and port."
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"The SMTP server returned code {exc.smtp_code}. Check the provider's SMTP settings."
    if isinstance(exc, OSError):
        return prefix + f"{type(exc).__name__}: {str(exc)[:180]}. Check the SMTP host, port and network access."
    return prefix + f"{type(exc).__name__}. Check the SMTP settings."


def meeting_form(db, uid, recipient_id, recipient_name, key, submission_id=None):
    if recipient_id == uid:
        return
    local_tz = viewer_timezone()
    with st.form(f"request_{key}"):
        st.caption(f"Times are shown in your browser time zone: {local_tz}. The recipient will accept or decline.")
        day = st.date_input("Date (your time zone)", key=f"day_{key}")
        clock = st.time_input("Start time (your time zone)", key=f"time_{key}")
        duration = st.selectbox("Duration in minutes", [15, 30, 45, 60], index=1, key=f"duration_{key}")
        purpose = st.text_area("Why would you like to meet?", max_chars=2000, key=f"msg_{key}")
        submitted = st.form_submit_button("Request meeting")
    if not submitted:
        return
    local_start = datetime.combine(day, clock, tzinfo=local_tz)
    start = local_start.astimezone(timezone.utc)
    if start.astimezone(local_tz).replace(tzinfo=None) != datetime.combine(day, clock):
        st.info("That local time does not exist because the clocks change. Choose another time.")
        return
    end = start + timedelta(minutes=duration)
    if start <= datetime.now(timezone.utc):
        st.info("Choose a future meeting time in your time zone.")
        return
    if len(purpose.strip()) < 5:
        st.info("Add a short reason for the meeting (at least 5 characters).")
        return
    try:
        saved = db.table("meeting_requests").insert({
            "submission_id": submission_id,
            "requester_id": uid, "recipient_id": recipient_id,
            "purpose": purpose.strip(),
            "proposed_start": start.isoformat(),
            "proposed_end": end.isoformat(),
            "format": "online",
        }).execute()
    except Exception as exc:
        st.error(f"Could not save meeting request: {exc}")
        return
    st.success("Meeting request saved. You can follow it in My meetings.")
    if not saved.data:
        st.info("Email notifications could not be sent because the saved request ID was not returned.")
        return
    meeting_id = saved.data[0]["id"]
    for label, action in [
        ("Invitation", lambda: send_invitation_email(db, uid, meeting_id)),
        ("Confirmation", lambda: send_booking_email(db, recipient_name, start, end, meeting_id)),
    ]:
        try:
            action()
            st.success(label + " email accepted by the email provider.")
        except Exception as exc:
            st.info(label + " email could not be sent. " + smtp_issue(exc))


def submission_details(db, uid, item):
    st.subheader(item["title"])
    st.caption(item["presentation_category"].replace("_", " ").title() +
               " · " + (item.get("author_name") or "Author") +
               " · " + (item.get("organisation") or ""))
    abstract_tab, poster_tab, video_tab, comments_tab, meeting_tab = st.tabs(
        ["Abstract", "Poster", "Video", "Comments", "Meeting"])
    with abstract_tab:
        st.write(item["abstract_text"])
        if item.get("support_request"):
            st.write("Help or investment sought:", item["support_request"])
        if item.get("keywords"):
            st.caption("Keywords: " + ", ".join(item["keywords"]))
    with poster_tab:
        if item.get("poster_url"):
            st.link_button("Open public poster", item["poster_url"])
            thumb = poster_thumbnail(item["poster_url"])
            if thumb:
                st.markdown(
                    '<img src="' + escape(thumb, quote=True) +
                    '" alt="Poster preview" loading="lazy" '
                    'style="max-width:100%;max-height:75vh;object-fit:contain">',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No poster link was provided.")
    with video_tab:
        if item.get("video_url"):
            try:
                st.video(item["video_url"])
            except Exception:
                st.link_button("Open public video", item["video_url"])
        else:
            st.info("No video link was provided.")
    with comments_tab:
        try:
            comments = db.rpc("platform_public_comments", {"p_submission_id": item["id"]}).execute().data or []
        except Exception as exc:
            st.error(f"Could not load comments: {exc}")
            return
        for comment in comments:
            prefix = "↳ " if comment.get("parent_key") else ""
            st.write(f"{prefix}**{comment['display_name']}:** {comment['comment_text']}")
            if uid == item["owner_id"] and not comment.get("parent_key"):
                with st.form("reply_" + comment["comment_key"]):
                    reply = st.text_area("Reply as the author", max_chars=2000)
                    send_reply = st.form_submit_button("Reply")
                if send_reply:
                    if len(reply.strip()) < 2:
                        st.info("Write a short reply.")
                    else:
                        try:
                            db.rpc("platform_author_reply", {
                                "p_submission_id": item["id"],
                                "p_parent_key": comment["comment_key"],
                                "p_reply": reply.strip(),
                            }).execute()
                            st.success("Reply posted.")
                            st.write("↳ **You (author):** " + reply.strip())
                        except Exception as exc:
                            st.error(f"Could not post reply: {exc}")
        with st.form("feedback_" + item["id"]):
            if not uid:
                st.caption("Your name and comment will be public. Your email stays private.")
                guest_name = st.text_input("Your name", max_chars=120)
                guest_email = st.text_input("Your email (private)", max_chars=254)
            comment_text = st.text_area("Leave a comment", max_chars=2000)
            send = st.form_submit_button("Post comment")
        if send:
            if len(comment_text.strip()) < 2:
                st.info("Write a short comment before posting.")
            else:
                try:
                    if uid:
                        db.table("comments").insert({
                            "submission_id": item["id"], "author_id": uid,
                            "comment_text": comment_text.strip(),
                        }).execute()
                        st.success("Comment posted.")
                        st.write("**You:** " + comment_text.strip())
                    else:
                        existing_keys = {row["comment_key"] for row in comments}
                        db.rpc("platform_guest_comment", {
                            "p_submission_id": item["id"], "p_name": guest_name.strip(),
                            "p_email": guest_email.strip(), "p_comment": comment_text.strip(),
                        }).execute()
                        latest = db.rpc("platform_public_comments", {
                            "p_submission_id": item["id"],
                        }).execute().data or []
                        published = any(
                            row["comment_key"] not in existing_keys and
                            row["display_name"] == guest_name.strip() and
                            row["comment_text"] == comment_text.strip()
                            for row in latest
                        )
                        if published:
                            st.success("Comment posted and visible publicly.")
                            st.write(f"**{guest_name.strip()}:** {comment_text.strip()}")
                        else:
                            st.info("Comment saved. The database update for immediate publishing has not been applied yet.")
                except Exception as exc:
                    st.error(f"Could not post comment: {exc}")
    with meeting_tab:
        if uid:
            meeting_form(db, uid, item["owner_id"],
                         item.get("author_name") or "the author", item["id"], item["id"])
        else:
            st.caption("Sign in to request or accept a meeting.")


def browse(db, uid, events):
    try:
        items = db.rpc("platform_public_gallery").execute().data or []
    except Exception as exc:
        st.info("The public gallery is being activated. The organiser needs to apply public_gallery_setup.sql in Supabase.")
        return
    selected_id = st.query_params.get("submission")
    if selected_id:
        if st.button("← Back to gallery"):
            del st.query_params["submission"]
            st.rerun()
        item = next((x for x in items if x["id"] == selected_id), None)
        if item:
            submission_details(db, uid, item)
        else:
            st.info("This submission is not available publicly.")
        return
    st.subheader("Explore projects, posters and abstracts")
    category = st.selectbox("Category", ["All", "Project idea", "Oral presentation", "Poster"])
    query = st.text_input("Search title, author, user, keyword or abstract")
    event_titles = {"All events": None, **{e["title"]: e["id"] for e in events}}
    selected_event = st.selectbox("Event", list(event_titles), key="browse_event")
    wanted = category.lower().replace(" ", "_")
    items = [x for x in items if
             (category == "All" or x["presentation_category"] == wanted) and
             (selected_event == "All events" or x["event_id"] == event_titles[selected_event]) and
             query.casefold() in " ".join([
                 x.get("title") or "", x.get("abstract_text") or "",
                 x.get("author_name") or "", x.get("organisation") or "",
                 " ".join(x.get("keywords") or []),
             ]).casefold()]
    if not items:
        st.info("No matching approved submissions yet.")
        return
    st.caption(f"{len(items)} result(s). Open a card for the full abstract, poster, video and comments.")
    columns = st.columns(3)
    for index, item in enumerate(items):
        with columns[index % 3]:
            with st.container(border=True):
                poster = item.get("poster_url")
                st.markdown("### 🖼️" if poster else "### 📄")
                st.markdown("**" + item["title"] + "**")
                st.caption(item["presentation_category"].replace("_", " ").title() +
                           " · " + (item.get("author_name") or "Author"))
                if st.button("View full submission", key="open_" + item["id"]):
                    st.query_params["submission"] = item["id"]
                    st.rerun()


def directory(db, uid):
    st.subheader("Meet participants")
    st.caption("Request a meeting with any registered participant, even if they have no abstract.")
    try:
        people = db.table("profiles").select("id,full_name,organisation,job_title").order("full_name").execute().data or []
    except Exception as exc:
        st.error(f"Could not load participants: {exc}")
        return
    query = st.text_input("Find a participant")
    matches = [
        p for p in people if p["id"] != uid and query.lower() in
        " ".join([p.get("full_name") or "", p.get("organisation") or "", p.get("job_title") or ""]).lower()
    ]
    if not matches:
        st.info("No matching participants yet.")
    for person in matches:
        name = person.get("full_name") or "Participant"
        with st.expander(name + (" · " + person["organisation"] if person.get("organisation") else "")):
            if person.get("job_title"):
                st.caption(person["job_title"])
            meeting_form(db, uid, person["id"], name, "person_" + person["id"])


def meetings(db, uid):
    st.subheader("Meeting requests")
    notice = st.session_state.pop("meeting_email_notice", None)
    if notice:
        st.info(notice)
    try:
        rows = db.table("meeting_requests").select("*").order("proposed_start").execute().data
        abstracts = db.table("submissions").select("id,title").execute().data
        titles = {a["id"]: a["title"] for a in abstracts}
    except Exception as exc:
        st.error(f"Could not load meetings: {exc}")
        return
    if not rows:
        st.info("There are no meeting requests yet.")
    local_tz = viewer_timezone()
    st.caption(f"Meeting times in your browser time zone: {local_tz}")
    for row in rows:
        role = "Author" if row["recipient_id"] == uid else "Requester"
        with st.expander(f"{titles.get(row['submission_id'], 'Direct meeting')} · {row['status']} · {role}"):
            st.write("Proposed start:", display_meeting_time(row["proposed_start"], local_tz))
            st.write("Proposed end:", display_meeting_time(row["proposed_end"], local_tz))
            st.write("Purpose:", row["purpose"])
            if row["status"] == "accepted":
                st.download_button("Add to calendar (reminder 1 hour before)", calendar_invitation(row), file_name=f"octa-meeting-{row['id']}.ics", mime="text/calendar", key=f"calendar_{row['id']}")
            if row["status"] == "accepted" and row.get("format") == "online":
                call_url = meeting_call_url(row)
                st.link_button("Open full Jitsi call", call_url)
                st.caption("The first participant must use Jitsi's Log-in button to start the room. The other participant can then join.")
            if row["status"] == "accepted" and row["recipient_id"] == uid:
                if st.button("Retry acceptance emails", key=f"retry_accept_email_{row['id']}"):
                    try:
                        send_acceptance_email(db, uid, row["id"])
                        st.success("Acceptance emails accepted by the email provider.")
                    except Exception as exc:
                        st.info(smtp_issue(exc))
                st.caption("Use retry only if delivery failed. Resend suppresses duplicate acceptance emails within 24 hours.")
            if row.get("private_message"):
                st.write("Private message:", row["private_message"])
            if row["recipient_id"] == uid and row["status"] == "pending":
                left, right = st.columns(2)
                if left.button("Accept", key=f"accept_{row['id']}"):
                    try:
                        accepted = db.table("meeting_requests").update({"status": "accepted"}).eq(
                            "id", row["id"]).eq("recipient_id", uid).eq("status", "pending").execute()
                    except Exception as exc:
                        st.error(f"Could not accept request: {exc}")
                    else:
                        if accepted.data:
                            try:
                                send_acceptance_email(db, uid, row["id"])
                                st.session_state.meeting_email_notice = "Meeting accepted. Confirmation emails with a meeting link and calendar attachment were accepted by the email provider for both participants."
                            except Exception as exc:
                                st.session_state.meeting_email_notice = "Meeting accepted, but confirmation emails could not be sent. " + smtp_issue(exc)
                        else:
                            st.session_state.meeting_email_notice = "This request is no longer pending. Refresh its status in My meetings."
                        st.rerun()
                if right.button("Decline", key=f"decline_{row['id']}"):
                    try:
                        db.table("meeting_requests").update({"status": "declined"}).eq("id", row["id"]).execute()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not decline request: {exc}")
            if row["requester_id"] == uid and row["status"] == "pending":
                if st.button("Cancel request", key=f"cancel_{row['id']}"):
                    try:
                        db.table("meeting_requests").delete().eq("id", row["id"]).execute()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not cancel request: {exc}")


def manage_submission_content(db, event):
    with st.expander("Remove submissions, posters or videos"):
        try:
            rows = db.rpc("platform_admin_submissions", {"p_event_id": event["id"]}).execute().data or []
        except Exception:
            st.info("Run admin_removal_setup.sql in Supabase SQL Editor to enable removal tools.")
            return
        if not rows:
            st.info("No submissions in this event.")
            return
        with st.form("remove_content_" + event["id"]):
            item = st.selectbox("Submission", rows, format_func=lambda r: r["title"] + " · " + r["status"])
            action = st.selectbox("Remove", ["Entire abstract / submission", "Poster link only", "Video link only"])
            st.caption("Deleting the entire submission also removes its comments, replies and associated meetings. Removing a link does not delete the externally hosted file.")
            confirmed = st.checkbox("I confirm this removal")
            remove = st.form_submit_button("Remove selected content")
        if remove:
            if not confirmed:
                st.info("Confirm the removal first.")
                return
            try:
                db.rpc("platform_remove_submission_content", {
                    "p_submission_id": item["id"],
                    "p_action": {"Entire abstract / submission": "submission", "Poster link only": "poster", "Video link only": "video"}[action],
                }).execute()
                st.session_state.removal_notice = "Selected content removed."
                st.rerun()
            except Exception as exc:
                st.error(f"Could not remove content: {exc}")


def super_remove_accounts_events(db, events):
    with st.expander("Delete a user account"):
        try:
            users = db.rpc("platform_admin_users").execute().data or []
        except Exception:
            st.info("Run admin_removal_setup.sql in Supabase SQL Editor to enable removal tools.")
            users = []
        if users:
            with st.form("remove_user"):
                person = st.selectbox("User to remove", users, format_func=lambda u: (u.get("full_name") or "Participant") + " · " + (u.get("email") or u["user_id"]))
                st.caption("This permanently deletes the account, its submissions, comments, replies and meetings. The super admin account is protected.")
                confirmation = st.text_input("Type the user's email to confirm")
                remove_user = st.form_submit_button("Permanently delete user")
            if remove_user:
                if not person.get("email") or confirmation.strip().lower() != person["email"].lower():
                    st.info("Enter the selected user's email exactly.")
                else:
                    try:
                        db.rpc("platform_remove_user", {"p_user_id": person["user_id"]}).execute()
                        st.session_state.removal_notice = "User account and associated app data removed."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not remove user. The database operation was rolled back: {exc}")
    if events:
        with st.expander("Delete an event"):
            with st.form("remove_event"):
                event = st.selectbox("Event to remove", events, format_func=lambda e: e["title"])
                st.caption("This permanently deletes the event, all its submissions and their comments and meetings. Participant accounts remain.")
                confirmation = st.text_input("Type the event title to confirm")
                remove_event = st.form_submit_button("Permanently delete event")
            if remove_event:
                if confirmation.strip() != event["title"]:
                    st.info("Enter the selected event title exactly.")
                else:
                    try:
                        db.rpc("platform_remove_event", {"p_event_id": event["id"]}).execute()
                        st.session_state.removal_notice = "Event and associated submissions removed."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not remove event: {exc}")


def review_event(db, event):
    manage_submission_content(db, event)
    st.subheader("Review: " + event["title"])
    try:
        items = db.rpc("platform_review_queue", {"p_event_id": event["id"]}).execute().data or []
    except Exception as exc:
        st.error(f"Could not load review queue: {exc}")
        return
    if not items:
        st.info("No projects, posters or abstracts are awaiting review for this event.")
    for item in items:
        with st.expander(item["title"]):
            st.caption("Category: " + (item.get("presentation_category") or "project_idea").replace("_", " ").title())
            st.caption(f"Submitted: {item.get('submitted_at') or item.get('created_at')}")
            st.write(item["abstract_text"])
            if item.get("support_request"):
                st.write("Help or investment sought:", item["support_request"])
            if item.get("short_summary"):
                st.write("Summary:", item["short_summary"])
            if item.get("thematic_area"):
                st.write("Thematic area:", item["thematic_area"])
            if item.get("keywords"):
                st.write("Keywords:", ", ".join(item["keywords"]))
            if item.get("poster_url"):
                st.link_button("Open poster", item["poster_url"])
            if item.get("video_url"):
                st.link_button("Open video", item["video_url"])
            approve, reject = st.columns(2)
            decision = None
            if approve.button("Approve", key=f"approve_{item['id']}"):
                decision = "approved"
            if reject.button("Reject", key=f"reject_{item['id']}"):
                decision = "rejected"
            if decision:
                try:
                    db.rpc("platform_review_submission", {
                        "p_submission_id": item["id"], "p_decision": decision,
                    }).execute()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not review submission: {exc}")


def admin_content(db, events):
    st.markdown("**Create participants and submissions**")
    # Check the signed-in account before constructing any privileged client.
    if not db.rpc("platform_is_super_admin").execute().data:
        st.error("Super admin access required.")
        return
    secret_key = st.secrets.get("SUPABASE_SECRET_KEY") or st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not secret_key:
        st.info("To enable these admin tools, add SUPABASE_SECRET_KEY (or the legacy SUPABASE_SERVICE_ROLE_KEY) to Streamlit App settings → Secrets. Keep the key private.")
        return
    try:
        admin = create_client(
            st.secrets["SUPABASE_URL"], secret_key,
            options=SyncClientOptions(auto_refresh_token=False, persist_session=False),
        )
    except Exception as exc:
        st.error("Could not initialize the Supabase admin connection. No account was created.")
        st.caption("Diagnostic: " + type(exc).__name__)
        return
    bulk_import_panel(db, admin, events)
    with st.expander("Create a participant account"):
        st.caption("Set a normal sign-in password. The participant can keep it or change it from their account. Share it privately.")
        with st.form("admin_create_participant", clear_on_submit=True):
            name = st.text_input("Participant name", max_chars=120)
            email = st.text_input("Participant email", max_chars=254)
            organisation = st.text_input("Organisation", max_chars=160)
            password = st.text_input("Password (at least 8 characters)", type="password")
            confirm = st.text_input("Confirm password", type="password")
            verified = st.checkbox("I verified that this email belongs to the participant")
            create = st.form_submit_button("Create participant")
        if create:
            if len(name.strip()) < 2 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
                st.info("Enter the participant's name and a valid email address.")
            elif len(password) < 8 or password != confirm:
                st.info("Enter matching passwords of at least 8 characters.")
            elif not verified:
                st.info("Verify the participant's email address before creating a confirmed account.")
            else:
                try:
                    response = admin.auth.admin.create_user({
                        "email": email.strip().lower(), "password": password,
                        "email_confirm": True, "user_metadata": {"full_name": name.strip()},
                    })
                    if not response.user:
                        raise ValueError("Supabase did not return a new user")
                except Exception as exc:
                    st.error(f"Could not create account: {exc}")
                else:
                    try:
                        uid = response.user.id
                        admin.table("profiles").upsert({
                            "id": uid, "full_name": name.strip(),
                            "organisation": organisation.strip(),
                        }, on_conflict="id").execute()
                        admin.table("platform_user_types").upsert({
                            "user_id": uid, "user_type": "project_owner",
                        }, on_conflict="user_id").execute()
                        st.success(f"Account created for {email.strip()}. They can sign in with the password. Select them in Add a submission below.")
                    except Exception as exc:
                        st.error(f"Account was created for {email.strip()}, but its profile could not be saved: {exc}. Do not create the account again.")

    with st.expander("Add an abstract, poster and video for a participant"):
        try:
            people = admin.table("profiles").select("id,full_name,organisation").order("full_name").execute().data or []
        except Exception as exc:
            st.error(f"Could not load participants: {exc}")
            return
        if not people or not events:
            st.info("Create a participant and an event first.")
            return
        labels = {
            p["id"]: f"{p.get('full_name') or 'Participant'} · {p.get('organisation') or 'No organisation'} · {p['id'][:8]}"
            for p in people
        }
        with st.form("admin_add_submission", clear_on_submit=True):
            owner_id = st.selectbox("Author account", list(labels), format_func=lambda uid: labels[uid])
            event = st.selectbox("Event", events, format_func=lambda e: e["title"])
            category = st.selectbox("Category", ["Project idea", "Oral presentation", "Poster"])
            title = st.text_input("Title", max_chars=200)
            thematic_area = st.text_input("Thematic area (optional)", max_chars=160)
            summary = st.text_input("Short summary (optional)", max_chars=300)
            body = st.text_area("Full abstract", height=180, max_chars=8000)
            keywords = st.text_input("Keywords (comma-separated)", max_chars=300)
            support_request = st.text_area("Technical help, investment or collaboration sought (optional)", max_chars=2000)
            st.caption("Paste public HTTPS links: host the poster PDF/image on Google Drive or similar, and the video on YouTube or another open platform.")
            poster = st.text_input("Public poster URL (optional)")
            video = st.text_input("Public video URL (optional)")
            publish_now = st.checkbox("Publish immediately", value=True)
            add = st.form_submit_button("Add submission")
        if add:
            if len(title.strip()) < 5 or len(body.strip()) < 30:
                st.info("Add a title of at least 5 characters and an abstract of at least 30 characters.")
            elif not valid_url(poster) or not valid_url(video):
                st.info("Poster and video links must be public HTTPS addresses.")
            else:
                try:
                    reviewer_id = db.auth.get_user().user.id
                    now = datetime.now(timezone.utc).isoformat()
                    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70] + "-" + uuid.uuid4().hex[:8]
                    status = "approved" if publish_now else "submitted"
                    admin.table("submissions").insert({
                        "owner_id": owner_id, "event_id": event["id"], "title": title.strip(), "slug": slug,
                        "abstract_text": body.strip(), "short_summary": summary.strip() or None,
                        "thematic_area": thematic_area.strip() or None,
                        "keywords": [word.strip() for word in keywords.split(",") if word.strip()],
                        "poster_url": poster.strip() or None, "video_url": video.strip() or None,
                        "presentation_category": category.lower().replace(" ", "_"),
                        "support_request": support_request.strip() or None,
                        "status": status, "submitted_at": now,
                        "approved_by": reviewer_id if publish_now else None,
                        "approved_at": now if publish_now else None,
                    }).execute()
                    st.success("Submission published." if publish_now else "Submission added to the review queue.")
                except Exception as exc:
                    st.error(f"Could not add submission: {exc}")


def super_dashboard(db, events):
    st.subheader("Super admin dashboard")
    super_remove_accounts_events(db, events)
    admin_content(db, events)
    with st.expander("Edit existing submissions"):
        submission_editor(db, events)
    st.markdown("**Test the Jitsi video call**")
    if "demo_jitsi_room" not in st.session_state:
        st.session_state.demo_jitsi_room = "OctaMatchmakingDemo" + uuid.uuid4().hex
    demo_url = "https://meet.jit.si/" + st.session_state.demo_jitsi_room
    st.link_button("Open test Jitsi room", demo_url)
    st.code(demo_url)
    st.caption("Open the link in another browser or send it to a colleague. The first participant must log in to Jitsi to start the room. This test does not create a booking or send email.")
    with st.form("create_event"):
        st.markdown("**Create event**")
        title = st.text_input("Event title", max_chars=200)
        description = st.text_area("Description")
        create = st.form_submit_button("Create event")
    if create:
        try:
            db.rpc("platform_create_event", {
                "p_title": title.strip(), "p_description": description.strip(),
                "p_starts_at": None, "p_ends_at": None,
            }).execute()
            st.success("Event created.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not create event: {exc}")
    st.markdown("**Email diagnostics**")
    missing = smtp_missing_settings()
    if missing:
        st.info("Booking emails are not configured. Missing Secrets: " + ", ".join(missing))
    else:
        if st.secrets.get("RESEND_API_KEY"):
            st.caption("Provider: Resend · sender: " + st.secrets["RESEND_FROM_EMAIL"])
        else:
            st.caption(f"Configured host: {st.secrets['SMTP_HOST']} · port: {st.secrets['SMTP_PORT']} · sender: {st.secrets['SMTP_FROM']}")
        st.caption("Send a test to your signed-in email to verify delivery.")
        if st.button("Send test email to me"):
            try:
                own_email = db.auth.get_user().user.email
                send_email(own_email, "Matchmaking email test",
                           "This confirms that meeting emails can be sent from the matchmaking app.")
                st.success("Test email sent to " + own_email + ". Check the inbox and spam folder.")
            except Exception as exc:
                st.info(smtp_issue(exc))
    if not events:
        return
    event = st.selectbox("Manage event", events, format_func=lambda e: e["title"])
    try:
        older = db.rpc("platform_unassigned_submissions").execute().data or []
        if older:
            st.markdown("**Submissions made before events were created**")
            for old in older:
                left, right = st.columns([4, 1])
                left.write(old["title"])
                if right.button("Assign to this event", key=f"assign_{old['id']}"):
                    db.rpc("platform_assign_submission", {
                        "p_submission_id": old["id"], "p_event_id": event["id"],
                    }).execute()
                    st.rerun()
    except Exception as exc:
        st.error(f"Could not load older submissions: {exc}")
    with st.form("grant_admin"):
        email = st.text_input("Registered user's email")
        grant = st.form_submit_button("Give event sub-admin rights")
    if grant:
        try:
            db.rpc("platform_grant_event_admin", {
                "p_event_id": event["id"], "p_email": email.strip(),
            }).execute()
            st.success("Event sub-admin added.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not add sub-admin: {exc}")
    try:
        admins = db.rpc("platform_list_event_admins", {"p_event_id": event["id"]}).execute().data or []
        for person in admins:
            left, right = st.columns([4, 1])
            left.write(person.get("full_name") or person["email"])
            if right.button("Remove", key=f"remove_{event['id']}_{person['user_id']}"):
                db.rpc("platform_revoke_event_admin", {
                    "p_event_id": event["id"], "p_user_id": person["user_id"],
                }).execute()
                st.rerun()
    except Exception as exc:
        st.error(f"Could not load event admins: {exc}")
    review_event(db, event)


def participant_type(db, uid):
    rows = db.table("platform_user_types").select("user_type").eq("user_id", uid).execute().data
    if rows:
        return rows[0]["user_type"]
    st.subheader("Choose how you will participate")
    labels = {
        "Project owner": "project_owner",
        "Investor": "investor",
        "Audience": "audience",
    }
    choice = st.radio("Your role", list(labels))
    if st.button("Continue"):
        try:
            db.table("platform_user_types").insert({
                "user_id": uid, "user_type": labels[choice],
            }).execute()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save your role: {exc}")
    st.stop()


def meeting_alert_data(db, uid, now):
    # Explicitly scope alerts to this participant, including for super admins.
    rows = db.table("meeting_requests").select(
        "id,requester_id,recipient_id,status,proposed_start,proposed_end"
    ).or_(f"requester_id.eq.{uid},recipient_id.eq.{uid}").in_(
        "status", ["pending", "accepted"]).execute().data or []
    incoming = [r for r in rows if r["recipient_id"] == uid and r["status"] == "pending"]
    accepted = [r for r in rows if r["status"] == "accepted"
                and parse_meeting_time(r["proposed_end"]) > now]
    soon = [r for r in accepted if parse_meeting_time(r["proposed_start"]) <= now + timedelta(hours=24)]
    soon.sort(key=lambda r: parse_meeting_time(r["proposed_start"]))
    signals = {f"invitation:{r['id']}" for r in incoming}
    signals.update(f"accepted:{r['id']}" for r in accepted)
    signals.update(f"soon:{r['id']}" for r in soon)
    return incoming, accepted, soon, signals


@st.fragment(run_every="30s")
def live_meeting_alerts(db, uid, sidebar_slot):
    # Polling does not renew the eight-hour idle session.
    sid = st.session_state.get("login_session_id")
    if sid:
        try:
            store = session_store()
            if store and not store.read(sid):
                forget_tokens()
                st.rerun()
        except Exception:
            st.caption("Meeting alerts could not verify your session. Refresh to reconnect.")
            return
    try:
        incoming, accepted, soon, signals = meeting_alert_data(db, uid, datetime.now(timezone.utc))
    except Exception:
        with sidebar_slot.container():
            st.caption("Meeting alerts temporarily unavailable.")
        st.caption("Could not refresh meeting alerts. They will be checked again automatically.")
        return
    seen_key = "meeting_alert_seen_" + uid
    seen = set(st.session_state.get(seen_key, []))
    fresh = signals - seen
    st.session_state[seen_key] = list(seen | signals)
    event_key = "meeting_alert_event_" + uid
    if fresh:
        st.session_state[event_key] = st.session_state.get(event_key, 0) + 1
    with sidebar_slot.container():
        if incoming:
            st.warning(f"🔔 {len(incoming)} incoming meeting request(s)")
        if soon:
            st.success(f"📅 {len(soon)} meeting(s) in the next 24 hours")
            for row in soon[:3]:
                st.caption(display_meeting_time(row["proposed_start"], viewer_timezone()))
        elif accepted:
            st.info(f"📅 {len(accepted)} upcoming accepted meeting(s)")
    if signals:
        messages = []
        if incoming:
            messages.append(f"🔔 {len(incoming)} meeting invitation(s) waiting for your reply")
        if soon:
            messages.append(f"📅 {len(soon)} meeting(s) in the next 24 hours")
        elif accepted:
            messages.append(f"🤝 {len(accepted)} upcoming accepted meeting(s)")
        st.markdown(
            '<div role="status" aria-live="polite" style="background:linear-gradient(120deg,#1c3e82,#075e53);'
            'color:#fff;border-left:8px solid #ffd166;border-radius:12px;padding:20px 24px;margin:8px 0 12px">'
            '<div style="font-size:1.65rem;font-weight:900;line-height:1.4">'
            + '<br>'.join(escape(m) for m in messages)
            + '</div><div style="font-size:1rem;margin-top:8px">Open <strong>My meetings</strong> in the sidebar for details and meeting links.</div></div>',
            unsafe_allow_html=True,
        )
        if fresh:
            st.balloons()
            st.toast("You have a meeting update. Open My meetings.", icon="🔔")
    meeting_alert_sound(event=st.session_state.get(event_key, 0),
        has_alerts=bool(signals), key="meeting_sound_" + uid, default=None)
    st.caption("Meeting alerts update every 30 seconds while this app is connected. Sound is optional.")



def available_events(db):
    return db.table("platform_events").select("*").order("created_at", desc=True).execute().data or []




sync_login_cookie()
db = client()
if st.session_state.get("removal_notice"):
    st.success(st.session_state.pop("removal_notice"))
if not st.session_state.get("recovery_tokens"):
    callback_url = read_recovery_fragment(default=None, key="recovery_fragment")
    if callback_url:
        tokens = recovery_tokens_from_url(callback_url)
        if tokens:
            st.session_state.recovery_tokens = tokens
            st.rerun()
if st.query_params.get("recovery_token") or st.session_state.get("recovery_tokens"):
    with st.sidebar:
        sidebar_footer()
    password_recovery_screen(db)
    st.stop()
uid = user_id(db)
try:
    events = available_events(db)
except Exception as exc:
    st.error(f"Could not load events: {exc}")
    st.stop()
if not uid:
    with st.sidebar:
        public_page = st.radio("Navigate", ["Browse", "Sign in or create account"], key="public_page")
        sidebar_footer()
    if st.session_state.pop("reset_done", False):
        st.success("Password changed. Sign in with your new password.")
    if public_page == "Browse":
        browse(db, None, events)
    else:
        auth_screen(db)
    st.stop()

with st.sidebar:
    try:
        profile = db.table("profiles").select("full_name").eq("id", uid).execute().data or []
        welcome_name = (profile[0].get("full_name") or "").strip() if profile else ""
        if not welcome_name:
            account = db.auth.get_user().user
            welcome_name = account.user_metadata.get("full_name") or account.user_metadata.get("name") or (account.email or "Participant").split("@")[0]
    except Exception:
        welcome_name = "Participant"
    st.markdown("Welcome")
    st.markdown(
        '<div style="font-size:1.65rem;font-weight:700;color:#36b583;line-height:1.25;overflow-wrap:anywhere;margin-bottom:1rem">'
        + escape(str(welcome_name)) + '</div>',
        unsafe_allow_html=True,
    )
    meeting_sidebar_slot = st.empty()
    st.caption("Sign-in expires after 8 hours without activity.")
    if st.session_state.get("session_warning"):
        st.info(st.session_state.session_warning)
    with st.expander("Change my password"):
        with st.form("change_account_password", clear_on_submit=True):
            new_password = st.text_input("New password (at least 8 characters)", type="password", key="account_new_password")
            confirm_password = st.text_input("Confirm new password", type="password", key="account_confirm_password")
            change_password = st.form_submit_button("Change password")
        if change_password:
            if len(new_password) < 8 or new_password != confirm_password:
                st.info("Use at least 8 characters and enter the same password twice.")
            else:
                try:
                    db.auth.update_user({"password": new_password})
                    st.success("Password changed. Use your new password next time you sign in.")
                except Exception:
                    st.error("Could not change the password. Sign out, sign in again, and retry.")
    if st.button("Sign out"):
        try:
            db.auth.sign_out()
        finally:
            forget_tokens()
            st.rerun()

live_meeting_alerts(db, uid, meeting_sidebar_slot)
save_profile(db, uid)
try:
    is_super = bool(db.rpc("platform_is_super_admin").execute().data)
    kind = participant_type(db, uid)
except Exception as exc:
    st.info("The event and user-role database setup is not active yet. Run multi_role_setup.sql in Supabase.")
    st.stop()

admin_events = []
if not is_super:
    try:
        admin_events = [
            e for e in events
            if db.rpc("platform_is_event_admin", {"p_event_id": e["id"]}).execute().data
        ]
    except Exception as exc:
        st.error(f"Could not check event permissions: {exc}")
        st.stop()

role_options = {"Project owner": "project_owner", "Investor": "investor", "Audience": "audience"}
role_labels = list(role_options)
current_label = next(label for label, value in role_options.items() if value == kind)
chosen_label = st.sidebar.selectbox("Act as", role_labels, index=role_labels.index(current_label))
if role_options[chosen_label] != kind:
    try:
        db.table("platform_user_types").update({
            "user_type": role_options[chosen_label],
        }).eq("user_id", uid).execute()
        st.rerun()
    except Exception as exc:
        st.sidebar.error(f"Could not switch role: {exc}")


pages = ["Browse projects / posters / abstracts", "My submissions", "Meet participants", "My meetings"]
if kind == "project_owner" and not is_super:
    pages.insert(1, "Submit project / poster / abstract")
if is_super:
    pages.append("Super admin")
elif admin_events:
    pages.append("Event admin")
st.sidebar.caption("Super admin" if is_super else kind.replace("_", " ").title())
page = st.sidebar.radio("Navigate", pages)
sidebar_footer()
if page == "Browse projects / posters / abstracts":
    browse(db, uid, events)
elif page == "Submit project / poster / abstract":
    publish(db, uid, events)
elif page == "My submissions":
    submission_editor(db, events)
elif page == "Meet participants":
    directory(db, uid)
elif page == "My meetings":
    meetings(db, uid)
elif page == "Super admin":
    super_dashboard(db, events)
else:
    event = st.selectbox("Your event", admin_events, format_func=lambda e: e["title"])
    review_event(db, event)
