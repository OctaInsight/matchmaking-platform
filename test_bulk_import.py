"""Run with: python -m unittest test_bulk_import.py (no live accounts created)."""
import io
import unittest
import importlib.util
import sys
import zipfile
from types import SimpleNamespace as NS
from unittest.mock import Mock, MagicMock, patch
from xml.etree import ElementTree as ET

from bulk_import import (
    FIELDS, HEADERS, read_excel, plan_import, preview_rows, import_rows,
    submission_payload, list_accounts,
)
from bulk_template import template_bytes


def row(name="Test User", email="new@example.com", **values):
    return {**dict.fromkeys(FIELDS, ""), "name": name, "email": email, "_row": 2, **values}


def fixture_workbook(values, formula=False):
    # Add a test-only row to the real bundled template, retaining its exact headers.
    source = zipfile.ZipFile(io.BytesIO(template_bytes()))
    xml = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    data = xml.find(namespace + "sheetData")
    for item in list(data):
        if item.get("r") == "2":
            data.remove(item)
    record = ET.Element(namespace + "row", r="2")
    data.insert(1, record)
    for index, value in enumerate(values):
        if value == "":
            continue
        cell = ET.SubElement(record, namespace + "c", r=f"{chr(65+index)}2", t="inlineStr")
        if formula and index == 0:
            ET.SubElement(cell, namespace + "f").text = 'HYPERLINK("https://example.com")'
        else:
            inline = ET.SubElement(cell, namespace + "is")
            ET.SubElement(inline, namespace + "t").text = value
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in source.namelist():
            archive.writestr(name, ET.tostring(xml) if name == "xl/worksheets/sheet1.xml" else source.read(name))
    return output.getvalue()


class Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.filters, self.operation, self.payload = [], "select", None
    def select(self, *args, **kwargs): return self
    def eq(self, key, value): self.filters.append((key,value)); return self
    def insert(self, payload): self.operation="insert"; self.payload=dict(payload); return self
    def upsert(self, payload, **kwargs): self.operation="upsert"; self.payload=dict(payload); return self
    def execute(self):
        records=self.db.records[self.table_name]
        if self.operation=="select":
            return NS(data=[r for r in records if all(r.get(k)==v for k,v in self.filters)])
        if self.db.fail_profile and self.table_name=="profiles":
            self.db.fail_profile=False
            raise RuntimeError("private-password-should-not-be-displayed")
        records.append(self.payload)
        return NS(data=[self.payload])


class Admin:
    def __init__(self):
        self.records={"profiles":[],"platform_user_types":[],"submissions":[]}
        self.fail_profile=False
        self.accounts={}
        self.auth=NS(admin=NS(create_user=Mock(side_effect=self.create), list_users=Mock(side_effect=self.list)))
    def table(self, name): return Query(self,name)
    def create(self,payload):
        uid="id-"+payload["email"]
        self.accounts[payload["email"]]=uid
        return NS(user=NS(id=uid))
    def list(self, page=1, per_page=1000):
        return [NS(email=email,id=uid) for email,uid in self.accounts.items()]


class BulkImportTests(unittest.TestCase):
    events=[{"id":"event-1","title":"CloudEARTHi Conference"}]
    def plan(self, rows, accounts=None):
        plans,errors=plan_import(rows,accounts or {},self.events,"event-1")
        self.assertEqual(errors,[])
        return plans
    def test_real_template_and_required_only_row(self):
        with self.assertRaisesRegex(ValueError,"no participant"):
            read_excel(template_bytes())
        rows=read_excel(fixture_workbook(["Test User","new@example.com"]))
        self.assertEqual(rows[0]["email"],"new@example.com")
        plans=self.plan(rows)
        self.assertFalse(plans[0]["submission"])
        admin=Admin()
        results,credentials=import_rows(admin,plans,{},"reviewer")
        self.assertEqual(results[0]["Account"],"Created")
        self.assertGreaterEqual(len(credentials[0]["Password"]),8)
        self.assertEqual(len(admin.records["submissions"]),0)
        self.assertEqual(admin.records["profiles"][0]["full_name"],"Test User")
    def test_existing_user_submission_preserves_profile_password_role(self):
        admin=Admin()
        admin.accounts={"existing@example.com":"existing"}
        admin.records["profiles"]=[{"id":"existing","full_name":"Existing Name"}]
        admin.records["platform_user_types"]=[{"user_id":"existing","user_type":"investor"}]
        plan=self.plan([row(email="existing@example.com",password="short",
            title="Contribution title",abstract="A complete abstract with at least thirty characters.",publish="Yes")],admin.accounts)
        result,credentials=import_rows(admin,plan,dict(admin.accounts),"reviewer")
        admin.auth.admin.create_user.assert_not_called()
        self.assertEqual(credentials,[])
        self.assertEqual(admin.records["profiles"][0]["full_name"],"Existing Name")
        self.assertEqual(admin.records["platform_user_types"][0]["user_type"],"investor")
        self.assertEqual(result[0]["Submission"],"Published")
    def test_combined_and_retry_are_idempotent(self):
        rows=[row(password="SuppliedPassword",title="Contribution title",
                  abstract="A complete abstract with at least thirty characters.",publish="Yes"),
              row(_row=3,title="Another contribution",
                  abstract="Another complete abstract with at least thirty characters.")]
        admin=Admin()
        plans=self.plan(rows)
        results,credentials=import_rows(admin,plans,{},"reviewer")
        self.assertEqual(admin.auth.admin.create_user.call_count,1)
        self.assertEqual(len(admin.records["submissions"]),2)
        plans=self.plan(rows,admin.accounts)
        results,_=import_rows(admin,plans,dict(admin.accounts),"reviewer",credentials)
        self.assertEqual(len(admin.records["submissions"]),2)
        self.assertTrue(all("Skipped" in r["Submission"] for r in results))
        self.assertEqual(len(credentials),1)
    def test_incomplete_submission_is_never_published(self):
        plans=self.plan([row(poster_url="https://example.com/poster.pdf",publish="Yes")])
        self.assertTrue(plans[0]["incomplete"])
        self.assertEqual(plans[0]["status"],"submitted")
        payload=submission_payload(plans[0],"owner","reviewer")
        self.assertIsNone(payload["approved_by"])
        self.assertIn("not provided",payload["abstract_text"])
    def test_bad_rows_rejected_and_preview_hides_password(self):
        _,errors=plan_import([row(password="short",abstract="x"*8001)],{},self.events,"event-1")
        self.assertTrue(errors)
        plans=self.plan([row(password="SecretPassword!")])
        self.assertNotIn("SecretPassword!",str(preview_rows(plans)))
        with self.assertRaisesRegex(ValueError,"formulas"):
            read_excel(fixture_workbook(["Test User","new@example.com"],formula=True))
    def test_partial_failure_keeps_credentials_and_retry_repairs_profile(self):
        admin=Admin()
        admin.fail_profile=True
        rows=[row()]
        result,credentials=import_rows(admin,self.plan(rows),{},"reviewer")
        self.assertEqual(result[0]["Account"],"Created")
        self.assertTrue(result[0]["Error"])
        self.assertNotIn("private-password",result[0]["Error"])
        self.assertEqual(len(credentials),1)
        result,_=import_rows(admin,self.plan(rows,admin.accounts),dict(admin.accounts),"reviewer",credentials)
        self.assertEqual(result[0]["Error"],"")
        self.assertEqual(admin.auth.admin.create_user.call_count,1)
        self.assertEqual(len(admin.records["profiles"]),1)
    def test_account_pagination(self):
        api=Mock()
        api.list_users.side_effect=[
            [NS(email=f"user{i}@example.com",id=str(i)) for i in range(1000)],
            [NS(email="last@example.com",id="last")],
        ]
        users=list_accounts(NS(auth=NS(admin=api)))
        self.assertEqual(len(users),1001)
        self.assertEqual(api.list_users.call_args.kwargs["page"],2)
    def test_panel_blocks_non_super_admin(self):
        st=MagicMock()
        db=NS(rpc=lambda name: NS(execute=lambda: NS(data=False)))
        admin=Admin()
        with patch.dict(sys.modules, {"streamlit":st}):
            spec=importlib.util.spec_from_file_location("test_bulk_ui","bulk_import_ui.py")
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.bulk_import_panel(db,admin,self.events)
        st.error.assert_called_once_with("Super admin access required.")
        st.download_button.assert_not_called()
        admin.auth.admin.list_users.assert_not_called()
        admin.auth.admin.create_user.assert_not_called()
    def test_default_event_category_and_conflicting_passwords(self):
        plans=self.plan([row(title="A supplied title")])
        self.assertEqual(plans[0]["event_id"],"event-1")
        self.assertEqual(plans[0]["category"],"project_idea")
        _,errors=plan_import([row(password="PasswordOne"),row(_row=3,password="PasswordTwo")],{},self.events,"event-1")
        self.assertTrue(errors)


if __name__=="__main__":
    unittest.main()
