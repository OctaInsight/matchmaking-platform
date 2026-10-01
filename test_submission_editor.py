import ast
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.parse import urlparse

tree = ast.parse(open("submission_editor.py").read())
ns = {"urlparse": urlparse}
nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))
         and (isinstance(n, ast.Assign) or n.name in ("validate_changes", "save_submission"))]
exec(compile(ast.Module(body=nodes, type_ignores=[]), "submission_editor.py", "exec"), ns)


class Query:
    def __init__(self, admin): self.admin=admin; self.filters=[]; self.values=None
    def select(self, *args): return self
    def eq(self, key, value): self.filters.append((key,value)); return self
    def update(self, values): self.values=values; return self
    def execute(self):
        match=all(self.admin.row.get(k)==v for k,v in self.filters)
        if self.values is not None:
            self.admin.writes.append(self.filters)
            if match: self.admin.row.update(self.values)
        return NS(data=[self.admin.row.copy()] if match else [])


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.admin=NS(row={"id":"record", "owner_id":"owner", "status":"approved",
                           "event_id":"conference", "slug":"original-slug"}, writes=[])
        self.admin.table=lambda name: Query(self.admin)
        ns["editor_access"]=Mock(return_value=(self.admin,"owner",False))
        self.values=dict(title="Updated title",abstract_text="Updated abstract with more than thirty characters.",
            short_summary=None,thematic_area=None,keywords=[],poster_url="https://example.com/poster.pdf",
            video_url=None,presentation_category="poster",support_request=None)
    def test_owner_updates_content_preserves_relationships(self):
        ns["save_submission"](None,"record",self.values)
        self.assertEqual(self.admin.row["title"],"Updated title")
        self.assertEqual(self.admin.row["status"],"approved")
        self.assertEqual(self.admin.row["event_id"],"conference")
        self.assertEqual(self.admin.row["id"],"record")
        self.assertIn(("owner_id","owner"),self.admin.writes[0])
    def test_other_user_cannot_edit(self):
        ns["editor_access"].return_value=(self.admin,"intruder",False)
        with self.assertRaises(ValueError): ns["save_submission"](None,"record",self.values)
        self.assertEqual(self.admin.writes,[])
    def test_super_admin_can_edit_other_authors_record(self):
        ns["editor_access"].return_value=(self.admin,"super",True)
        ns["save_submission"](None,"record",self.values)
        self.assertEqual(self.admin.row["title"],"Updated title")
    def test_protected_fields_and_invalid_content_rejected(self):
        for changes in (dict(self.values,owner_id="intruder"),dict(self.values,status="approved"),
                        dict(self.values,abstract_text="x"*8001),dict(self.values,poster_url="http://example.com")):
            with self.assertRaises(ValueError): ns["save_submission"](None,"record",changes)
        self.assertEqual(self.admin.writes,[])


if __name__ == "__main__": unittest.main()
