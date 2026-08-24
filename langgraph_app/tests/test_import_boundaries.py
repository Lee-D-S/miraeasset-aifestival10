import ast
import unittest
from pathlib import Path


class ImportBoundaryTests(unittest.TestCase):
    def test_nodes_do_not_import_other_nodes(self):
        nodes_dir = Path(__file__).parents[1] / "nodes"
        for path in nodes_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
            for node in imports:
                imported = ast.unparse(node)
                self.assertNotIn("langgraph_app.nodes.", imported, path.name)


if __name__ == "__main__":
    unittest.main()

