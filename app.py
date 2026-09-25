import re
import uuid
import smtplib
import socket
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Matchmaking Platform", page_icon="🤝", layout="wide")
st.title("Project & Innovation Matchmaking")
APP_URL = "https://octa-matchmaking.streamlit.app/"


def client():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_ANON_KEY"]
    except (KeyError, FileNotFoundError):
        st.error("Supabase settings are missing. Add SUPABASE_URL and SUPABASE_ANON_KEY to Streamlit secrets.")
        st.stop()
    db = create_client(url, key)
    tokens = st.session_state.get("tokens")
    if tokens:
        try:
            response = db.auth.set_session(tokens["access_token"], tokens["refresh_token"])
            if response.session:
                st.session_state.tokens = {
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                }
        except Exception:
            st.session_state.pop("tokens", None)
            st.rerun()
    return db


def user_id(db):
    if not st.session_state.get("tokens"):
        return None
    try:
        return db.auth.get_user().user.id
    except Exception:
        st.session_state.pop("tokens", None)
        st.rerun()


def valid_url(value, hosts=None):
    if not value:
        return True
    p = urlparse(value.strip())
    return p.scheme == "https" and bool(p.netloc) and (
        hosts is None or p.hostname in hosts
    )


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
    sign_in, sign_up = st.tabs(["Sign in", "Create account"])
    with sign_in:
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            try:
                res = db.auth.sign_in_with_password({"email": email.strip(), "password": password})
                st.session_state.tokens = {
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }
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
                        st.session_state.tokens = {
                            "access_token": res.session.access_token,
                            "refresh_token": res.session.refresh_token,
                        }
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


def publish(db, uid, events):
    st.subheader("Submit an abstract or innovation idea")
    with st.form("abstract"):
        event_titles = {e["title"]: e["id"] for e in events if e.get("is_active")}
        if not event_titles:
            st.info("No event is open for submissions yet.")
            st.stop()
        event_title = st.selectbox("Event", list(event_titles))
        title = st.text_input("Title", max_chars=200, help="At least 5 characters.")
        thematic_area = st.text_input("Thematic area (optional)", max_chars=160)
        summary = st.text_input("Short summary (optional)", max_chars=300)
        body = st.text_area("Abstract", height=180, max_chars=5000, help="At least 30 characters.")
        keywords = st.text_input("Keywords", max_chars=300)
        poster = st.text_input("Public poster URL (image or PDF, optional)")
        video = st.text_input("Public YouTube video URL (optional)")
        submitted = st.form_submit_button("Submit for approval")
    if submitted:
        if len(title.strip()) < 5 or len(body.strip()) < 30:
            st.info("To submit, add a title of at least 5 characters and an abstract of at least 30 characters.")
        elif not valid_url(poster) or not valid_url(video, {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"}):
            st.info("Check the links: both need HTTPS, and the video link must be from YouTube.")
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
                    "status": "submitted", "submitted_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                st.success("Your submission was sent for approval. It will appear publicly once approved.")
            except Exception as exc:
                st.error(f"Could not publish: {exc}")


def smtp_missing_settings():
    needed = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
    return [name for name in needed if not st.secrets.get(name)]


class EmailStageError(Exception):
    def __init__(self, stage, original):
        self.stage = stage
        self.original = original
        super().__init__(str(original))


def send_email(to_address, subject, body):
    missing = smtp_missing_settings()
    if missing:
        raise ValueError("Missing Streamlit Secrets: " + ", ".join(missing))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = st.secrets["SMTP_FROM"]
    msg["To"] = to_address
    msg.set_content(body)
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


def send_booking_email(db, recipient_name, start, end):
    user = db.auth.get_user().user
    if not user or not user.email:
        raise ValueError("Your account has no email address")
    send_email(
        user.email,
        "Your matchmaking meeting request",
        f"Your meeting request for {recipient_name} has been submitted.\n\n"
        f"Proposed time (UTC): {start.strftime('%Y-%m-%d %H:%M')}–"
        f"{end.strftime('%H:%M')}\n\n"
        "The recipient still needs to accept it. You can check its status in My meetings.",
    )


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
    with st.form(f"request_{key}"):
        st.caption("Propose a meeting time in UTC. The recipient will accept or decline.")
        day = st.date_input("Date (UTC)", key=f"day_{key}")
        clock = st.time_input("Start time (UTC)", key=f"time_{key}")
        duration = st.selectbox("Duration in minutes", [15, 30, 45, 60], index=1, key=f"duration_{key}")
        purpose = st.text_area("Why would you like to meet?", max_chars=2000, key=f"msg_{key}")
        submitted = st.form_submit_button("Request meeting")
    if not submitted:
        return
    start = datetime.combine(day, clock, tzinfo=timezone.utc)
    end = start + timedelta(minutes=duration)
    if start <= datetime.now(timezone.utc):
        st.info("Choose a future meeting time in UTC.")
        return
    if len(purpose.strip()) < 5:
        st.info("Add a short reason for the meeting (at least 5 characters).")
        return
    try:
        db.table("meeting_requests").insert({
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
    try:
        send_booking_email(db, recipient_name, start, end)
        st.success("Meeting request saved. A confirmation email was sent to you.")
    except Exception as exc:
        st.success("Meeting request saved. You can follow it in My meetings.")
        st.info("The confirmation email was not sent. " + smtp_issue(exc))


def browse(db, uid, events):
    st.subheader("Browse abstracts")
    event_titles = {e["title"]: e["id"] for e in events}
    if not event_titles:
        st.info("There are no events yet.")
        return
    selected_event = st.selectbox("Event", list(event_titles), key="browse_event")
    try:
        items = db.table("submissions").select("*").eq("event_id", event_titles[selected_event]).eq("status", "approved").order("created_at", desc=True).execute().data
        people = db.table("profiles").select("id,full_name,organisation").execute().data
        names = {person["id"]: person for person in people}
    except Exception as exc:
        st.error(f"Could not load abstracts: {exc}")
        return
    query = st.text_input("Search titles, abstracts and keywords")
    items = [x for x in items if query.lower() in " ".join(
        [x["title"], x["abstract_text"], " ".join(x.get("keywords") or [])]
    ).lower()]
    if not items:
        st.info("No approved submissions yet. Newly submitted abstracts appear after approval.")
    for item in items:
        author = names.get(item["owner_id"], {})
        with st.expander(item["title"] + " · " + (item.get("thematic_area") or "Innovation")):
            st.caption(f"By {author.get('full_name', 'Participant')} · {author.get('organisation', '')}")
            st.write(item["abstract_text"])
            if item.get("keywords"):
                st.caption("Keywords: " + ", ".join(item["keywords"]))
            if item.get("poster_url"):
                url = item["poster_url"]
                st.link_button("Open poster", url)
                if re.search(r"\.(png|jpe?g|webp)(\?.*)?$", url, re.I):
                    st.image(url)
            if item.get("video_url"):
                st.video(item["video_url"])
            meeting_form(db, uid, item["owner_id"], author.get("full_name") or "the author", item["id"], item["id"])
            st.markdown("**Feedback**")
            comments = db.table("comments").select("*").eq("submission_id", item["id"]).eq("status", "visible").order("created_at").execute().data
            for comment in comments:
                who = names.get(comment["author_id"], {}).get("full_name", "Participant")
                st.write(f"**{who}:** {comment['comment_text']}")
            with st.form(f"feedback_{item['id']}"):
                comment = st.text_area("Leave feedback", max_chars=2000)
                send = st.form_submit_button("Post feedback")
            if send:
                if len(comment.strip()) < 2:
                    st.info("Write a short comment before posting.")
                else:
                    try:
                        db.table("comments").insert({
                            "submission_id": item["id"], "author_id": uid, "comment_text": comment.strip()
                        }).execute()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not post feedback: {exc}")


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
    try:
        rows = db.table("meeting_requests").select("*").order("proposed_start").execute().data
        abstracts = db.table("submissions").select("id,title").execute().data
        titles = {a["id"]: a["title"] for a in abstracts}
    except Exception as exc:
        st.error(f"Could not load meetings: {exc}")
        return
    if not rows:
        st.info("There are no meeting requests yet.")
    for row in rows:
        role = "Author" if row["recipient_id"] == uid else "Requester"
        with st.expander(f"{titles.get(row['submission_id'], 'Direct meeting')} · {row['status']} · {role}"):
            st.write("Proposed start (UTC):", row["proposed_start"])
            st.write("Proposed end (UTC):", row["proposed_end"])
            st.write("Purpose:", row["purpose"])
            if row["status"] == "accepted" and row.get("format") == "online":
                room = "OctaMatchmaking" + row["id"].replace("-", "")
                call_url = "https://meet.jit.si/" + room
                st.link_button("Open Jitsi call", call_url)
                st.caption("The first person to start a room on meet.jit.si may need to sign in as moderator.")
                if st.button("Show call here", key=f"jitsi_{row['id']}"):
                    st.session_state["show_jitsi_" + row["id"]] = True
                if st.session_state.get("show_jitsi_" + row["id"]):
                    st.components.v1.iframe(call_url, height=600, scrolling=False)
            if row.get("private_message"):
                st.write("Private message:", row["private_message"])
            if row["recipient_id"] == uid and row["status"] == "pending":
                left, right = st.columns(2)
                if left.button("Accept", key=f"accept_{row['id']}"):
                    try:
                        db.table("meeting_requests").update({"status": "accepted"}).eq("id", row["id"]).execute()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not accept request: {exc}")
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


def review_event(db, event):
    st.subheader("Review: " + event["title"])
    try:
        items = db.rpc("platform_review_queue", {"p_event_id": event["id"]}).execute().data or []
    except Exception as exc:
        st.error(f"Could not load review queue: {exc}")
        return
    if not items:
        st.info("No abstracts are awaiting review for this event.")
    for item in items:
        with st.expander(item["title"]):
            st.caption(f"Submitted: {item.get('submitted_at') or item.get('created_at')}")
            st.write(item["abstract_text"])
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


def super_dashboard(db, events):
    st.subheader("Super admin dashboard")
    st.markdown("**Test the Jitsi video call**")
    if "demo_jitsi_room" not in st.session_state:
        st.session_state.demo_jitsi_room = "OctaMatchmakingDemo" + uuid.uuid4().hex
    demo_url = "https://meet.jit.si/" + st.session_state.demo_jitsi_room
    st.link_button("Open test Jitsi room", demo_url)
    st.code(demo_url)
    st.caption("Open the link in another browser or send it to a colleague to test a two-person call. This test does not create a booking or send email.")
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
            st.markdown("**Abstracts submitted before events were created**")
            for old in older:
                left, right = st.columns([4, 1])
                left.write(old["title"])
                if right.button("Assign to this event", key=f"assign_{old['id']}"):
                    db.rpc("platform_assign_submission", {
                        "p_submission_id": old["id"], "p_event_id": event["id"],
                    }).execute()
                    st.rerun()
    except Exception as exc:
        st.error(f"Could not load older abstracts: {exc}")
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


def available_events(db):
    return db.table("platform_events").select("*").order("created_at", desc=True).execute().data or []




db = client()
uid = user_id(db)
if not uid:
    st.write("Discover projects and innovation ideas, connect with authors, and arrange meetings.")
    auth_screen(db)
    st.stop()

with st.sidebar:
    st.write("Signed in")
    if st.button("Sign out"):
        db.auth.sign_out()
        st.session_state.pop("tokens", None)
        st.rerun()

save_profile(db, uid)
try:
    is_super = bool(db.rpc("platform_is_super_admin").execute().data)
    events = available_events(db)
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

try:
    incoming = db.table("meeting_requests").select("id", count="exact").eq("recipient_id", uid).eq("status", "pending").execute()
    if incoming.count:
        st.sidebar.info(f"{incoming.count} incoming meeting request(s) in My meetings")
except Exception:
    pass

pages = ["Browse abstracts", "Meet participants", "My meetings"]
if kind == "project_owner" and not is_super:
    pages.insert(1, "Submit an abstract")
if is_super:
    pages.append("Super admin")
elif admin_events:
    pages.append("Event admin")
st.sidebar.caption("Super admin" if is_super else kind.replace("_", " ").title())
page = st.sidebar.radio("Navigate", pages)
if page == "Browse abstracts":
    browse(db, uid, events)
elif page == "Submit an abstract":
    publish(db, uid, events)
elif page == "Meet participants":
    directory(db, uid)
elif page == "My meetings":
    meetings(db, uid)
elif page == "Super admin":
    super_dashboard(db, events)
else:
    event = st.selectbox("Your event", admin_events, format_func=lambda e: e["title"])
    review_event(db, event)
