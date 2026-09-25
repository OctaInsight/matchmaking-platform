import re
from datetime import datetime, timezone
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
            st.error("Please enter your full name.")
        else:
            try:
                db.table("profiles").insert({
                    "id": uid, "full_name": name.strip(),
                    "organisation": organisation.strip(), "bio": bio.strip()
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
                st.error("Use at least 8 characters.")
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
                    st.error(f"Account creation failed: {exc}")


    st.markdown("**Confirmation email expired?**")
    resend_email = st.text_input("Account email", key="resend_email")
    if st.button("Resend confirmation email"):
        if not resend_email.strip():
            st.error("Enter your account email.")
        else:
            try:
                db.auth.resend({
                    "type": "signup", "email": resend_email.strip(),
                    "options": {"email_redirect_to": APP_URL},
                })
                st.success("If confirmation is still needed, check your inbox for a new email.")
            except Exception as exc:
                st.error(f"Could not resend confirmation: {exc}")


def publish(db, uid):
    st.subheader("Submit an abstract or innovation idea")
    with st.form("abstract"):
        title = st.text_input("Title", max_chars=200)
        category = st.selectbox("Event", ["Project Brokerage", "Scale2Connect Matchmaking", "Other"])
        body = st.text_area("Abstract", height=180, max_chars=5000)
        keywords = st.text_input("Keywords", max_chars=300)
        poster = st.text_input("Public poster URL (image or PDF, optional)")
        video = st.text_input("Public YouTube video URL (optional)")
        submitted = st.form_submit_button("Publish")
    if submitted:
        if len(title.strip()) < 5 or len(body.strip()) < 30:
            st.error("Add a title of at least 5 characters and an abstract of at least 30 characters.")
        elif not valid_url(poster) or not valid_url(video, {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"}):
            st.error("Use HTTPS URLs; the video must be on YouTube.")
        else:
            try:
                db.table("abstracts").insert({
                    "author_id": uid, "title": title.strip(), "category": category,
                    "abstract_text": body.strip(), "keywords": keywords.strip(),
                    "poster_url": poster.strip() or None, "video_url": video.strip() or None,
                }).execute()
                st.success("Your abstract is published.")
            except Exception as exc:
                st.error(f"Could not publish: {exc}")


def request_form(db, item, uid):
    if item["author_id"] == uid:
        return
    with st.form(f"request_{item['id']}"):
        st.caption("Request a meeting with the author. Enter a date and time in UTC.")
        day = st.date_input("Date (UTC)", key=f"day_{item['id']}")
        clock = st.time_input("Time (UTC)", key=f"time_{item['id']}")
        message = st.text_area("Why would you like to meet?", max_chars=2000, key=f"msg_{item['id']}")
        submitted = st.form_submit_button("Send meeting request")
    if submitted:
        when = datetime.combine(day, clock, tzinfo=timezone.utc)
        if when <= datetime.now(timezone.utc):
            st.error("Choose a future time.")
        elif len(message.strip()) < 5:
            st.error("Please add a short message.")
        else:
            try:
                db.table("meeting_requests").insert({
                    "abstract_id": item["id"], "requester_id": uid,
                    "author_id": item["author_id"], "message": message.strip(),
                    "proposed_at": when.isoformat(),
                }).execute()
                st.success("Meeting request sent.")
            except Exception as exc:
                st.error(f"Could not send request: {exc}")


def browse(db, uid):
    st.subheader("Browse abstracts")
    try:
        items = db.table("abstracts").select("*").eq("status", "published").order("created_at", desc=True).execute().data
        people = db.table("profiles").select("id,full_name,organisation").execute().data
        names = {person["id"]: person for person in people}
    except Exception as exc:
        st.error(f"Could not load abstracts: {exc}")
        return
    query = st.text_input("Search titles, abstracts and keywords")
    items = [x for x in items if query.lower() in " ".join(
        [x["title"], x["abstract_text"], x.get("keywords") or ""]
    ).lower()]
    if not items:
        st.info("No matching abstracts yet.")
    for item in items:
        author = names.get(item["author_id"], {})
        with st.expander(item["title"] + " · " + item["category"]):
            st.caption(f"By {author.get('full_name', 'Participant')} · {author.get('organisation', '')}")
            st.write(item["abstract_text"])
            if item.get("keywords"):
                st.caption("Keywords: " + item["keywords"])
            if item.get("poster_url"):
                url = item["poster_url"]
                st.link_button("Open poster", url)
                if re.search(r"\.(png|jpe?g|webp)(\?.*)?$", url, re.I):
                    st.image(url)
            if item.get("video_url"):
                st.video(item["video_url"])
            request_form(db, item, uid)
            st.markdown("**Feedback**")
            comments = db.table("feedback").select("*").eq("abstract_id", item["id"]).order("created_at").execute().data
            for comment in comments:
                who = names.get(comment["sender_id"], {}).get("full_name", "Participant")
                st.write(f"**{who}:** {comment['comment']}")
            with st.form(f"feedback_{item['id']}"):
                comment = st.text_area("Leave feedback", max_chars=2000)
                send = st.form_submit_button("Post feedback")
            if send:
                if len(comment.strip()) < 2:
                    st.error("Enter a comment.")
                else:
                    try:
                        db.table("feedback").insert({
                            "abstract_id": item["id"], "sender_id": uid, "comment": comment.strip()
                        }).execute()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not post feedback: {exc}")


def meetings(db, uid):
    st.subheader("Meeting requests")
    try:
        rows = db.table("meeting_requests").select("*").order("proposed_at").execute().data
        abstracts = db.table("abstracts").select("id,title").execute().data
        titles = {a["id"]: a["title"] for a in abstracts}
    except Exception as exc:
        st.error(f"Could not load meetings: {exc}")
        return
    if not rows:
        st.info("There are no meeting requests yet.")
    for row in rows:
        role = "Author" if row["author_id"] == uid else "Requester"
        with st.expander(f"{titles.get(row['abstract_id'], 'Abstract')} · {row['status']} · {role}"):
            st.write("Proposed (UTC):", row["proposed_at"])
            st.write("Message:", row["message"])
            if row.get("meeting_link") and row["status"] == "accepted":
                st.link_button("Join online call", row["meeting_link"])
            if row["author_id"] == uid and row["status"] == "pending":
                link = st.text_input("Optional HTTPS call link (Zoom, Teams, Meet, etc.)", key=f"link_{row['id']}")
                left, right = st.columns(2)
                if left.button("Accept", key=f"accept_{row['id']}"):
                    if not valid_url(link):
                        st.error("Enter a valid HTTPS call link or leave it blank.")
                    else:
                        db.table("meeting_requests").update({
                            "status": "accepted", "meeting_link": link.strip() or None
                        }).eq("id", row["id"]).execute()
                        st.rerun()
                if right.button("Decline", key=f"decline_{row['id']}"):
                    db.table("meeting_requests").update({"status": "declined"}).eq("id", row["id"]).execute()
                    st.rerun()
            if row["requester_id"] == uid and row["status"] == "pending":
                if st.button("Cancel request", key=f"cancel_{row['id']}"):
                    db.table("meeting_requests").update({"status": "cancelled"}).eq("id", row["id"]).execute()
                    st.rerun()


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
page = st.sidebar.radio("Navigate", ["Browse abstracts", "Submit an abstract", "My meetings"])
if page == "Browse abstracts":
    browse(db, uid)
elif page == "Submit an abstract":
    publish(db, uid)
else:
    meetings(db, uid)
