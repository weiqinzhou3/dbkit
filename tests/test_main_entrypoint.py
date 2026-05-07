import io
import unittest
from contextlib import redirect_stdout


class MainEntrypointTest(unittest.TestCase):
    def test_main_entrypoint_delegates_to_dbkit_cli(self) -> None:
        import main

        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main.main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("DBKit", output.getvalue())


if __name__ == "__main__":
    unittest.main()
