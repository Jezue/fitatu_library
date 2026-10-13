# Releasing

## Checklist

1. Refresh the recorded fixtures if the API may have changed since the last recording
   (what they cover: `tests/fixtures/README.md`):

   ```bash
   fitatu-api login -o session_data.json   # git-ignored; delete it when done
   python scripts/record_fixtures.py --session session_data.json
   git diff tests/fixtures/api_recording.json
   ```

   The script scrubs personal data and then runs a leak check that refuses to write the file
   if a personal value from the raw recording survived. Read the diff anyway: the user id must
   be the synthetic `1234567`, there must be no names, e-mail addresses or meal names of your
   own, and only the changes you expect. Run the tests against the new file before committing it.
   The scenario logs the first catalog hit for "jajko kurze", so when search ranks another product
   first, tests that pin its recorded values (the totals in `tests/test_scripts.py`) need updating.
2. Set the version in `pyproject.toml` and `src/fitatu_api/__init__.py`.
3. In `CHANGELOG.md`, date the release's section: `## [X.Y.Z] - Unreleased` becomes
   `## [X.Y.Z] - YYYY-MM-DD` (changes collected under a bare `## [Unreleased]` get that heading
   instead).
4. Run the checks CI runs:

   ```bash
   rm -rf dist                # python -m build adds to dist/ and never removes older builds
   hatch run all              # ruff, mypy (src, demo.py, example.py), pytest, python -m build
   pre-commit run --all-files
   ```

5. Check the artifacts in `dist/`:

   ```bash
   python -m tarfile -l dist/fitatu_api-*.tar.gz
   python -m pip install twine && python -m twine check dist/*
   ```

6. Make sure `git status` shows no session files or other local artifacts, then tag the release
   (`git tag vX.Y.Z`), push the tag and upload with `python -m twine upload dist/*`.

## Versioning

The package is alpha. Keep version bumps small and say in the changelog entry what changed in
the public API, in auth and session handling, and what is not backwards compatible.
