# Golden files

Read this when adding, changing or regenerating golden or snapshot files.

- Verify and regenerate goldens only with the commands the skill's golden files line names.
- For a new test, state an expected value the requirement gives in the test, even beside a golden-file test, and add a
  golden file only for output too large to read there. Existing golden files stay unless their test is otherwise rewritten.
- Change a golden only when an intended rule change justifies it, and say why each golden changed in the change record a
  repository check requires (as the skill's golden files line names), else in the changelog or PR body.
- A golden that changes without an intended rule change is a failure to investigate.
  Never regenerate expected output merely to make a test pass.
- When a golden comes from a seeded or replayed run, pass the seed explicitly and keep the inputs needed to reproduce it.
