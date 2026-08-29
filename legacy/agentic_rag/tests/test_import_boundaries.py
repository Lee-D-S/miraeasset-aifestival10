import ast
import unittest
from pathlib import Path


class ImportBoundaryTests(unittest.TestCase):
    def test_agentic_does_not_import_legacy_backends(self):
        root = Path(__file__).parents[1]
        forbidden = {"rag", "langgraph_rag"}
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                    self.assertTrue(names.isdisjoint(forbidden), path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden, path)

