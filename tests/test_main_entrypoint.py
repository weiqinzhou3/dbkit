import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


class MainEntrypointTest(unittest.TestCase):
    def test_main_entrypoint_blocks_when_target_info_missing(self) -> None:
        """Without LLM and without target info, CLI must block and exit non-zero."""
        import main

        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "model:",
                        "  provider_kind: openai_compatible",
                        "  model_name: qwen3.5-flash",
                        "  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "  api_key: sk-test",
                        "runtime:",
                        f"  artifact_dir: {tmpdir}/artifacts",
                        "  invoke_llm: false",
                    ]
                ),
                encoding="utf-8",
            )

            with redirect_stdout(output):
                exit_code = main.main(
                    ["--config", str(config_path), "MySQL connection spike"]
                )

        # Phase-01.1: missing target info → blocked → exit 1
        self.assertEqual(exit_code, 1)
        self.assertIn("DBKit", output.getvalue())
        self.assertIn("status=blocked", output.getvalue())
        self.assertIn("missing_fields=", output.getvalue())
        self.assertIn("artifact=", output.getvalue())


if __name__ == "__main__":
    unittest.main()
