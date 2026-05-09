import tempfile
import unittest
from pathlib import Path

from dbkit.runtime.artifact_paths import (
    to_deepagents_repo_virtual_path,
    to_host_path,
    to_repo_relative_path,
)


class Phase03ArtifactPathTest(unittest.TestCase):
    def test_repo_relative_artifact_path_maps_to_repo_virtual_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            path = Path(".dbkit/artifacts/req_xxx.raw-evidence-index.json")

            virtual_path = to_deepagents_repo_virtual_path(path, repo_dir=repo)

        self.assertEqual(
            virtual_path,
            "/repo/.dbkit/artifacts/req_xxx.raw-evidence-index.json",
        )
        self.assertNotEqual(
            virtual_path,
            "/.dbkit/artifacts/req_xxx.raw-evidence-index.json",
        )

    def test_raw_content_ref_maps_to_repo_virtual_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            content_ref = ".dbkit/artifacts/raw/rawev_xxx.json"

            virtual_path = to_deepagents_repo_virtual_path(content_ref, repo_dir=repo)

        self.assertEqual(
            virtual_path,
            "/repo/.dbkit/artifacts/raw/rawev_xxx.json",
        )
        self.assertFalse(virtual_path.startswith("/.dbkit/"))

    def test_repo_virtual_path_maps_back_to_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)

            host_path = to_host_path(
                "/repo/.dbkit/artifacts/req_xxx.raw-evidence-index.json",
                repo_dir=repo,
            )

            self.assertEqual(
                host_path,
                repo / ".dbkit" / "artifacts" / "req_xxx.raw-evidence-index.json",
            )

    def test_host_path_under_repo_can_be_reported_as_repo_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            host_path = repo / ".dbkit" / "artifacts" / "req_xxx.raw-evidence-index.json"

            repo_relative = to_repo_relative_path(host_path, repo_dir=repo)

        self.assertEqual(
            repo_relative,
            ".dbkit/artifacts/req_xxx.raw-evidence-index.json",
        )


if __name__ == "__main__":
    unittest.main()
