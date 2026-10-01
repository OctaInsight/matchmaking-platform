"""Guarded editing of existing submissions through the private server client."""
import re
from urllib.parse import urlparse
import streamlit as st
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

EDIT_FIELDS = (
    "title", "abstract_text", "short_summary", "thematic_area", "keywords",
    "poster_url", "video_url", "presentation_category", "support_request",
)


def editor_access(db):
    account = db.auth.get_user().user
    if not account:
        raise ValueError("Sign in to edit submissions.")
    is_super = bool(db.rpc("platform_is_super_admin").execute().data)
    key = st.secrets.get("SUPABASE_SECRET_KEY") or st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise ValueError("Editing needs the existing server-side Supabase admin key.")
    admin = create_client(st.secrets["SUPABASE_URL"], key,
        options=SyncClientOptions(auto_refresh_token=False, persist_session=False))
    return admin, account.id, is_super


def validate_changes(values):
    if set(values) != set(EDIT_FIELDS):
        raise ValueError("Only submission content can be edited.")
    limits = {"title": 200, "abstract_text": 8000, "short_summary": 300,
              "thematic_area": 160, "support_request": 2000}
    if len(values["title"]) < 5 or len(values["abstract_text"]) < 30:
        raise ValueError("Use a title of at least 5 characters and an abstract of at least 30 characters.")
    for field, limit in limits.items():
        if len(values.get(field) or "") > limit:
            raise ValueError(field.replace("_", " ").title() + f" exceeds {limit} characters.")
    if values["presentation_category"] not in ("project_idea", "oral_presentation", "poster"):
        raise ValueError("Choose a supported category.")
    if not isinstance(values["keywords"], list) or len(", ".join(values["keywords"])) > 300:
        raise ValueError("Keywords must fit within 300 characters.")
    for field in ("poster_url", "video_url"):
        if values[field]:
            parsed = urlparse(values[field])
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("Poster and video links must use public HTTPS addresses.")


def save_submission(db, submission_id, values):
    validate_changes(values)
    # Recheck identity and permissions on every save; never trust a UI role flag.
    admin, uid, is_super = editor_access(db)
    query = admin.table("submissions").select("id,owner_id").eq("id", submission_id)
    if not is_super:
        query = query.eq("owner_id", uid)
    rows = query.execute().data or []
    if not rows or (not is_super and rows[0]["owner_id"] != uid):
        raise ValueError("This submission is unavailable or belongs to another participant.")
    update = admin.table("submissions").update(values).eq("id", submission_id)
    if not is_super:
        update = update.eq("owner_id", uid)
    result = update.execute().data or []
    if not result:
        raise ValueError("The submission could not be updated. Refresh and retry.")
    return result[0]


def submission_editor(db, events):
    st.subheader("Edit existing submissions")
    try:
        admin, uid, is_super = editor_access(db)
        query = admin.table("submissions").select("*").order("created_at", desc=True)
        if not is_super:
            query = query.eq("owner_id", uid)
        rows = query.execute().data or []
    except Exception as exc:
        st.info("Could not load submissions for editing. " + (str(exc) if isinstance(exc, ValueError) else "Check the server connection."))
        return
    if not rows:
        st.info("There are no existing submissions to edit.")
        return
    event_titles = {event["id"]: event["title"] for event in events}
    authors = {}
    if is_super:
        profiles = admin.table("profiles").select("id,full_name").execute().data or []
        authors = {p["id"]: p.get("full_name") or "Participant" for p in profiles}
    selected = st.selectbox("Choose an existing submission", rows,
        format_func=lambda r: r["title"] + " · " + r["status"] +
            (" · " + authors.get(r["owner_id"], "Participant") if is_super else "") +
            " · " + event_titles.get(r.get("event_id"), "No event"),
        key="edit_existing_submission")
    st.caption("Changes update this record directly. Its approval status, author, event, comments and meetings are retained.")
    categories = {"Project idea": "project_idea", "Oral presentation": "oral_presentation", "Poster": "poster"}
    labels = list(categories)
    category_index = next((i for i, label in enumerate(labels)
                           if categories[label] == selected.get("presentation_category")), 0)
    with st.form("edit_submission_" + selected["id"]):
        title = st.text_input("Title", value=selected.get("title") or "", max_chars=200)
        category = st.selectbox("Category", labels, index=category_index)
        thematic = st.text_input("Thematic area (optional)", value=selected.get("thematic_area") or "", max_chars=160)
        summary = st.text_input("Short summary (optional)", value=selected.get("short_summary") or "", max_chars=300)
        abstract = st.text_area("Full abstract", value=selected.get("abstract_text") or "", height=260, max_chars=8000)
        keywords = st.text_input("Keywords (comma-separated)", value=", ".join(selected.get("keywords") or []), max_chars=300)
        support = st.text_area("Technical help, investment or collaboration sought (optional)",
            value=selected.get("support_request") or "", max_chars=2000)
        st.caption("Upload the poster and video to an open-access host, then paste public HTTPS links here.")
        poster = st.text_input("Public poster URL (optional)", value=selected.get("poster_url") or "")
        video = st.text_input("Public video URL (optional)", value=selected.get("video_url") or "")
        save = st.form_submit_button("Save changes")
    if save:
        values = {
            "title": title.strip(), "presentation_category": categories[category],
            "thematic_area": thematic.strip() or None, "short_summary": summary.strip() or None,
            "abstract_text": abstract.strip(), "keywords": [k.strip() for k in keywords.split(",") if k.strip()],
            "support_request": support.strip() or None, "poster_url": poster.strip() or None,
            "video_url": video.strip() or None,
        }
        try:
            save_submission(db, selected["id"], values)
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Could not save changes. Your record may have been removed, or the server connection failed.")
        else:
            st.session_state.removal_notice = "Submission updated successfully."
            st.rerun()
