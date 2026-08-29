import ast
from pathlib import Path
import unittest


class BackendBoundaryTests(unittest.TestCase):
    def test_langgraph_backend_does_not_import_classic_rag(self):
        package_dir = Path(__file__).parents[1]
        for path in package_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name == "rag" or name.startswith("rag.") for name in names),
                    path.name,
                )

    def test_langgraph_scripts_do_not_import_classic_rag(self):
        scripts_dir = Path(__file__).parents[1].parent / "scripts"
        for path in scripts_dir.glob("langgraph_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name == "rag" or name.startswith("rag.") for name in names),
                    path.name,
                )


if __name__ == "__main__":
    unittest.main()
