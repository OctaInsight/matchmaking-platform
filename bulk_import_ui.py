"""Streamlit super-admin bulk upload panel."""
import hashlib
import streamlit as st
from bulk_import import (
    read_excel, list_accounts, plan_import, preview_rows, import_rows, credentials_csv,
)
from bulk_template import template_bytes


def bulk_import_panel(db, admin, events):
    with st.expander("Bulk upload users and abstracts from Excel"):
        if not db.rpc("platform_is_super_admin").execute().data:
            st.error("Super admin access required.")
            return
        st.write("Use one row per participant or contribution. Only Participant name (Username) and Participant email are required. Email is the sign-in username.")
        st.download_button("Download Excel import template", template_bytes(),
            file_name="Octa_Bulk_Import_Template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="bulk_template_download")
        st.caption("The columns match the participant and submission forms. Blank passwords generate random passwords for new users. Existing users keep their profiles and passwords.")
        event_map = {event["id"]: event["title"] for event in events}
        default_event = st.selectbox("Default event for rows with an empty Event cell",
            [None] + list(event_map), index=1 if events else 0,
            format_func=lambda value: event_map.get(value, "Users only / no default event"),
            key="bulk_default_event")
        st.caption("Leave submission fields blank for users only. Contributions without a title or abstract stay unpublished in the review queue. Blank Publish immediately means review.")
        upload = st.file_uploader("Upload completed Excel template (.xlsx, up to 500 rows)",
            type=["xlsx"], key="bulk_excel_upload")
        if upload is None:
            fingerprint = None
        else:
            fingerprint = hashlib.sha256(upload.getvalue()).hexdigest() + ":" + str(default_event)
        if st.session_state.get("bulk_fingerprint") != fingerprint:
            st.session_state.pop("bulk_preview", None)
            # Keep credentials/results from the last import until explicitly cleared.
            st.session_state.bulk_fingerprint = fingerprint
        if upload and st.button("Validate and preview import", key="bulk_preview_button"):
            st.session_state.pop("bulk_preview", None)
            try:
                rows = read_excel(upload.getvalue())
                accounts = list_accounts(admin)
                plans, errors = plan_import(rows, accounts, events, default_event)
                st.session_state.bulk_preview = {"plans": plans, "errors": errors,
                                                 "rows": rows, "completed": False}
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error("Could not prepare the import (" + type(exc).__name__ + "). Check the Supabase admin connection.")
        preview = st.session_state.get("bulk_preview")
        if preview:
            if preview["errors"]:
                st.error("Correct these rows and upload the file again. Nothing has been imported.")
                st.dataframe(preview["errors"], hide_index=True, use_container_width=True)
            else:
                st.dataframe(preview_rows(preview["plans"]), hide_index=True, use_container_width=True)
                st.caption("Passwords are hidden. The preview creates no accounts or submissions.")
                verified = st.checkbox("I verified that the email addresses of new participants belong to them",
                    key="bulk_verified_emails")
                confirmed = st.checkbox("I checked the preview and want to import these rows",
                    key="bulk_confirm_import")
                if st.button("Import previewed rows", key="bulk_run_import",
                             disabled=preview["completed"]):
                    needs_new = any(p["new_account"] for p in preview["plans"])
                    if not confirmed or (needs_new and not verified):
                        st.info("Check the preview confirmation and verify new participants' email addresses before importing.")
                    elif not db.rpc("platform_is_super_admin").execute().data:
                        st.error("Super admin access required.")
                    else:
                        try:
                            # Refresh account/event validation immediately before mutations.
                            accounts = list_accounts(admin)
                            plans, errors = plan_import(preview["rows"], accounts, events, default_event)
                            if errors:
                                st.error("Validation changed. Preview the file again.")
                            else:
                                reviewer_id = db.auth.get_user().user.id
                                existing_credentials = st.session_state.get("bulk_credentials", [])
                                st.session_state.bulk_credentials = existing_credentials
                                progress = st.progress(0.0)
                                results, credentials = import_rows(admin, plans, accounts, reviewer_id,
                                    credentials=existing_credentials, progress=progress.progress)
                                st.session_state.bulk_results = results
                                st.session_state.bulk_credentials = credentials
                                preview["completed"] = True
                        except Exception as exc:
                            st.error("Import stopped (" + type(exc).__name__ + "). Earlier successful steps may remain saved. Preview and retry the file.")
        results = st.session_state.get("bulk_results")
        if results:
            failed = sum(bool(row["Error"]) for row in results)
            st.info(f"Last import: {len(results)} row(s) processed; {failed} row(s) need attention.")
            st.dataframe(results, hide_index=True, use_container_width=True)
            st.caption("To retry failed rows, preview the workbook again. Identical bulk-imported submissions are skipped. Changed contribution content creates a new submission.")
        credentials = st.session_state.get("bulk_credentials")
        if credentials:
            st.warning("Download new-account passwords before closing this session. This private CSV contains passwords; share each one only with its participant.")
            st.download_button("Download private new-account credentials",
                credentials_csv(credentials), file_name="Octa_New_Account_Credentials.csv",
                mime="text/csv", key="bulk_credentials_download")
        if results or credentials:
            if st.button("Clear import results and passwords from this session", key="bulk_clear"):
                for key in ("bulk_results", "bulk_credentials", "bulk_preview"):
                    st.session_state.pop(key, None)
                st.rerun()
